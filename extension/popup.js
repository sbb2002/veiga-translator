// popup.js — Stage 1/2 have no real UI yet; this popup exists purely to
// start/stop capture and visually verify that partial/final transcripts +
// translations are arriving correctly (see PRD §7 for the
// partial/final distinction).

const tabId = Number(new URLSearchParams(location.search).get("tabId"));

const toggleBtn = document.getElementById("toggle");
const captureLabel = document.getElementById("captureLabel");
const liveDot = document.getElementById("liveDot");
const statusState = document.getElementById("statusState");
const statusDetail = document.getElementById("statusDetail");
const logEl = document.getElementById("log");
const scrollToBottomBtn = document.getElementById("scrollToBottomBtn");
const videoMetaEl = document.getElementById("videoMeta");
const videoMetaChannelEl = document.getElementById("videoMetaChannel");
const videoMetaTimeEl = document.getElementById("videoMetaTime");

const segmentEls = new Map(); // segment_id -> <div> element, so a "final" can replace its "partial"

// Live DOM cap (2026-08-30): background.js already caps the *persisted* log
// at MAX_LOG_ENTRIES (200), but nothing capped what actually stays rendered
// in this iframe — every segment ever seen kept its <div> in logEl and its
// entry in segmentEls forever, so a long-running session's node count (and
// the layout/scroll/ResizeObserver work done per frame) grew unbounded and
// the panel visibly started to stutter. Insertion order in a Map matches DOM
// order here (every new segment is appended, never reordered), so the
// oldest entry is always segmentEls' first key — pruning from there keeps
// the on-screen log a rolling window instead of an ever-growing one.
const MAX_LIVE_SEGMENTS = 50;

function pruneOldSegments() {
  while (segmentEls.size > MAX_LIVE_SEGMENTS) {
    const oldestId = segmentEls.keys().next().value;
    segmentEls.get(oldestId).container.remove();
    segmentEls.delete(oldestId);
  }
}

// Mirrors backend/config.py's FINAL_CONTEXT_HISTORY_SIZE (kept in sync
// manually, same reason as AUDIO_RMS_SILENCE_FLOOR etc. below — the
// extension can't import the Python config). AudioSession's _final_history
// is empty for the first few final sentences of a session, so those
// translations have no [PREVIOUS SENTENCE]/[PREVIOUS TRANSLATION] context to
// resolve dropped subjects/tone continuity — they read noticeably rougher
// than once the 3-sentence window fills (see IMPROVEMENT_BACKLOG.md/session
// discussion, 2026-08-25). Segments finalized before the window is full are
// tagged "warmup" and hidden unless the debug toggle is on, matching the
// same gating as the confidence bars (.conf-line).
const HISTORY_WARMUP_FINALS = 3;
let finalsSeenCount = 0;
const countedFinalSegments = new Set(); // guards against double-counting a re-sent final for the same segment

// Scroll-to-bottom affordance (2026-08-20, upgraded to continuous tracking
// 2026-08-29): the button used to just jump to the bottom once. Now clicking
// it re-arms `autoTrack`, which keeps the log stuck to the bottom through
// every subsequent arrival (a chat-log "follow" convention) until the viewer
// scrolls away from the bottom themselves, at which point tracking
// disengages and the button reappears. Distinguishing "our own scroll" from
// "the user scrolled" doesn't need a separate guard flag: every programmatic
// scroll here (scrollLogToBottom, restoreHistory's initial jump) sets
// scrollTop directly to scrollHeight, so it always lands AT the bottom —
// only a manual scroll can leave the log's scroll position short of it.
const SCROLL_BOTTOM_THRESHOLD_PX = 16;
let autoTrack = true;

function isScrolledToBottom() {
  return logEl.scrollHeight - logEl.scrollTop - logEl.clientHeight <= SCROLL_BOTTOM_THRESHOLD_PX;
}

function scrollLogToBottom() {
  logEl.scrollTop = logEl.scrollHeight;
}

// Visibility is driven by the actual scroll position (isScrolledToBottom()),
// not by the `autoTrack` intent flag — the two normally agree, but they can
// diverge when the log's scrollable area changes size without a "scroll"
// event firing (e.g. dragging the overlay panel's resize handle in
// content_script.js shrinks logEl's clientHeight while scrollTop stays put:
// no scroll event, but the log is now visibly short of the bottom). Checking
// the real position directly means the button is always correct regardless
// of what caused the position to change.
function updateScrollToBottomButton() {
  const atBottom = isScrolledToBottom();
  autoTrack = atBottom; // resync intent to match reality (see comment above)
  scrollToBottomBtn.classList.toggle("visible", !atBottom);
}

logEl.addEventListener("scroll", updateScrollToBottomButton);
// Panel resize (content_script.js's drag handles) changes logEl's
// clientHeight without a "scroll" event — re-check on every resize so the
// button never goes stale.
new ResizeObserver(updateScrollToBottomButton).observe(logEl);
scrollToBottomBtn.addEventListener("click", () => {
  autoTrack = true;
  scrollLogToBottom();
  updateScrollToBottomButton();
});

// restoreHistory() below awaits an async round-trip to fetch the persisted
// log, but the onMessage listener is already live the moment this script
// runs — a live TRANSCRIPT_EVENT for a brand-new segment can arrive and get
// appended to logEl while restoreHistory is still mid-fetch, landing above
// older history segments that haven't been appended yet (DOM order = first-
// append order). Buffer live events until history is restored, then replay
// them in arrival order, so segments always land in the log in the order
// they actually happened.
let restoring = true;
const pendingEvents = [];

let lastCaptureTitle = null; // Remember the tab title across state changes
let lastVideoMeta = null; // { channelName, videoTitle, streamStartedAt } — same "keep showing while idle" behavior as lastCaptureTitle

// Summary-bar state machine inputs (see renderContextSummaryArea): whether a
// capture session is live, whether any transcript has been rendered yet
// (live or restored), and the last real backend summary text.
let captureActive = false;
let transcriptSeen = false;
let contextSummaryText = "";

// Header 방송 제목: shown as "[한글 번역]\t[원문]" once the backend returns a
// JA→KO translation of the title, just the original until then. titleReqFor
// dedupes the one-shot request against the current title string.
let titleOriginal = "";
let titleTranslation = "";
let titleReqFor = "";
let titleReqAt = 0;

// 디버그 지표 라인용: ISO 8601 절대시각(streamStartedAt, content_script.js가
// ytInitialPlayerResponse에서 긁음) -> "X분 전"/"X시간 Y분 전" 표시. 렌더링
// 화면의 상대시간 배지를 다시 파싱하는 대신 절대시각 기준으로 매번 새로
// 계산하므로 언어/포맷 변화에 안전하다.
function formatElapsedSince(isoTimestamp) {
  if (!isoTimestamp) return null;
  const startMs = Date.parse(isoTimestamp);
  if (Number.isNaN(startMs)) return null;
  const elapsedMin = Math.max(0, Math.round((Date.now() - startMs) / 60000));
  if (elapsedMin < 60) return `${elapsedMin}분 전`;
  const hours = Math.floor(elapsedMin / 60);
  const mins = elapsedMin % 60;
  return `${hours}시간 ${mins}분 전`;
}

// Shared marquee helper: if `text` overflows `el`, swap in a .marquee-track
// with two back-to-back copies and let CSS scroll it seamlessly; otherwise
// plain text + ellipsis. Skips rebuilding when the text is unchanged so the
// 30s videoMeta refresh (and repeated refreshState calls) don't restart the
// scroll mid-loop. `el` needs the shared .marquee CSS (see popup.html).
const MARQUEE_PX_PER_SECOND = 40;
function renderMaybeMarquee(el, text) {
  const value = text ?? "";
  if (el.dataset.marqueeText === value) return;
  el.dataset.marqueeText = value;
  el.classList.remove("marquee");
  el.textContent = value;
  el.title = value;
  if (!value) return;
  requestAnimationFrame(() => {
    if (el.dataset.marqueeText !== value) return; // superseded while waiting
    if (el.scrollWidth - el.clientWidth <= 0) return; // fits — leave plain
    const textWidth = el.scrollWidth;
    el.textContent = "";
    const track = document.createElement("div");
    track.className = "marquee-track";
    const original = document.createElement("span");
    original.textContent = value;
    const loopCopy = document.createElement("span");
    loopCopy.textContent = value;
    loopCopy.setAttribute("aria-hidden", "true");
    track.append(original, loopCopy);
    el.append(track);
    el.classList.add("marquee");
    track.style.animationDuration = `${textWidth / MARQUEE_PX_PER_SECOND}s`;
  });
}

// 채널명(1행, 길면 marquee) / 방송 시작 경과시간(2행). 2026-08-29부터 디버그
// 모드와 무관하게 항상 노출 — 채널·시간이 모두 없을 때만 [hidden].
function clearChannelAvatar() {
  videoMetaEl.classList.remove("has-avatar");
  delete videoMetaEl.dataset.avatarUrl;
  videoMetaEl.style.removeProperty("--channel-avatar");
  videoMetaEl.style.removeProperty("--channel-avatar-tint");
}

// Sample an average colour from the avatar for the left-side gradient bleed
// (.video-meta::before). Needs the image CORS-readable — googleusercontent
// serves Access-Control-Allow-Origin:*, but if a draw ever taints the canvas
// the gradient just falls back to the row background (an invisible bleed).
function sampleAvatarTint(url) {
  const img = new Image();
  img.crossOrigin = "anonymous";
  img.onload = () => {
    try {
      const canvas = document.createElement("canvas");
      canvas.width = canvas.height = 16;
      const ctx = canvas.getContext("2d");
      ctx.drawImage(img, 0, 0, 16, 16);
      const data = ctx.getImageData(0, 0, 16, 16).data;
      let r = 0, g = 0, b = 0, n = 0;
      for (let i = 0; i < data.length; i += 4) {
        r += data[i]; g += data[i + 1]; b += data[i + 2]; n++;
      }
      if (videoMetaEl.dataset.avatarUrl === url) {
        videoMetaEl.style.setProperty(
          "--channel-avatar-tint",
          `rgb(${(r / n) | 0} ${(g / n) | 0} ${(b / n) | 0})`
        );
      }
    } catch {
      videoMetaEl.style.removeProperty("--channel-avatar-tint");
    }
  };
  img.onerror = () => videoMetaEl.style.removeProperty("--channel-avatar-tint");
  img.src = url;
}

function renderHeaderTitle() {
  if (!titleOriginal) {
    renderMaybeMarquee(statusDetail, "");
    return;
  }
  renderMaybeMarquee(
    statusDetail,
    titleTranslation ? `${titleTranslation}\t${titleOriginal}` : titleOriginal
  );
}

// Ask the backend to translate the current title JA→KO. offscreen.js drops
// the request when the capture WS isn't OPEN yet (common right after start)
// and there's no ack, so it's retried. Timing:
//   - panel/page (re)load, video change: fires from refreshState /
//     VIDEO_META_UPDATED (fresh iframe ⇒ no translation yet ⇒ sends).
//   - backend just came up: every transcript event retries until the first
//     translation lands (transcript flowing = WS live).
//   - every 3 min after that: the setInterval below calls this with
//     force=true to refresh a possibly-better translation.
// force bypasses the "already have one" guard; the 4s gate still prevents
// overlapping in-flight requests.
function requestTitleTranslation(force = false) {
  if (!titleOriginal || !captureActive) return;
  if (titleReqFor !== titleOriginal) {
    titleReqFor = titleOriginal;
    titleTranslation = "";
    titleReqAt = 0;
  }
  if (!force && titleTranslation) return; // already have it
  if (Date.now() - titleReqAt < 4000) return; // a request is still in flight
  titleReqAt = Date.now();
  chrome.runtime
    .sendMessage({
      type: "TRANSLATE_TITLE",
      tabId,
      text: titleOriginal,
      requestId: titleOriginal,
    })
    .catch(() => {});
}

// Periodic refresh (no-op while idle — requestTitleTranslation guards on
// captureActive). One short gemma call every 3 min is negligible.
setInterval(() => requestTitleTranslation(true), 180000);

chrome.runtime.onMessage.addListener((message) => {
  if (message?.type !== "TITLE_TRANSLATION") return;
  if (message.tabId !== tabId) return;
  const { text, translation } = message.data ?? {};
  if (!translation || text !== titleOriginal) return; // empty or stale
  titleTranslation = translation;
  renderHeaderTitle();
});

function renderVideoMeta(meta) {
  const channel = meta?.channelName ?? "";
  const elapsed = formatElapsedSince(meta?.streamStartedAt);
  if (!channel && !elapsed) {
    videoMetaEl.hidden = true;
    renderMaybeMarquee(videoMetaChannelEl, "");
    videoMetaTimeEl.textContent = "";
    clearChannelAvatar();
    return;
  }
  videoMetaEl.hidden = false;
  renderMaybeMarquee(videoMetaChannelEl, channel);
  videoMetaTimeEl.textContent = elapsed ? `방송 시작 ${elapsed}` : "";

  // Channel avatar (right, fully visible) + its left-side colour bleed. The
  // URL is scraped page markup — only feed it into url() after checking it's
  // a plain https URL with nothing that could break out of the quotes.
  const avatar = meta?.channelAvatarUrl ?? "";
  const safeAvatar = /^https:\/\/[^\s"')]+$/.test(avatar) ? avatar : "";
  if (!safeAvatar) {
    clearChannelAvatar();
  } else if (videoMetaEl.dataset.avatarUrl !== safeAvatar) {
    videoMetaEl.dataset.avatarUrl = safeAvatar;
    videoMetaEl.classList.add("has-avatar");
    videoMetaEl.style.setProperty("--channel-avatar", `url("${safeAvatar}")`);
    videoMetaEl.style.removeProperty("--channel-avatar-tint");
    sampleAvatarTint(safeAvatar);
  }
}

async function refreshState() {
  const state = await chrome.runtime.sendMessage({ type: "GET_CAPTURE_STATE", tabId });
  const isActive = state?.active ?? false;
  const isPaused = state?.paused ?? false;
  const isRecording = isActive && !isPaused; // has a live session AND is actively sending audio
  captureActive = isActive;

  // Update toggle button: toggle "recording" class (record vs pause icon —
  // see popup.html) and set label text via captureLabel. Three real states:
  // no session at all / actively capturing / paused-with-session-alive.
  toggleBtn.classList.toggle("recording", isRecording);
  if (isRecording) {
    captureLabel.textContent = "일시정지";
  } else if (isActive) {
    captureLabel.textContent = "재개";
  } else {
    captureLabel.textContent = "캡처 시작";
  }

  // Update status state label
  if (isRecording) {
    statusState.textContent = "캡처 중";
  } else if (isActive) {
    statusState.textContent = "일시정지됨";
  } else {
    statusState.textContent = "대기 중";
  }

  // Update status detail line (방송 제목, header top line)
  if (isActive) {
    lastCaptureTitle = state?.title ?? `탭 #${tabId}`;
    lastVideoMeta = {
      channelName: state?.channelName ?? null,
      channelAvatarUrl: state?.channelAvatarUrl ?? null,
      videoTitle: state?.videoTitle ?? null,
      streamStartedAt: state?.streamStartedAt ?? null,
    };
  }
  // Prefer the scraped video title (clean) over the browser tab title, which
  // carries a "(N) " unread-count prefix and a " - YouTube" suffix — the "(1) "
  // in particular makes the title translator continue "(2) (3) ..." instead of
  // translating.
  titleOriginal =
    (isActive && lastVideoMeta && lastVideoMeta.videoTitle) || lastCaptureTitle || "";
  renderHeaderTitle();
  requestTitleTranslation();
  renderVideoMeta(lastVideoMeta);

  renderContextSummaryArea();

  // Update live dot
  liveDot.classList.toggle("off", !isRecording);
}

// Diagnostic aid (2026-08-19): a hung background service worker previously
// left this handler awaiting forever with nothing visible anywhere — no
// error, no console output, button just never changes. Wrap every step so a
// hang surfaces as a timeout error instead of silence, and log at each step
// so the panel's own console shows exactly how far the click got.
function withTimeout(promise, label, ms = 5000) {
  return Promise.race([
    promise,
    new Promise((_, reject) =>
      setTimeout(() => reject(new Error(`${label} timed out after ${ms}ms`)), ms)
    ),
  ]);
}

// This file only ever runs inside the in-page overlay panel's iframe now
// (content_script.js builds it, background.js's action.onClicked triggers it
// — see there). Starting a session from nothing requires activeTab, which
// is only granted by the toolbar-icon click itself, not by a gesture inside
// this already-open iframe (confirmed live 2026-08-19: chrome.sidePanel had
// the exact same problem) — so a session with NO capture running yet can
// only be started via the toolbar icon. But pause/resume of an *existing*
// session (background.js's pauseCapture/resumeCapture) needs no such
// gesture — the tabCapture stream stays alive the whole time — so this
// button fully controls that part once a session exists.
toggleBtn.addEventListener("click", async () => {
  console.log("[popup] toggle clicked");
  try {
    const state = await withTimeout(
      chrome.runtime.sendMessage({ type: "GET_CAPTURE_STATE", tabId }),
      "GET_CAPTURE_STATE"
    );
    console.log("[popup] current state:", state);
    if (state?.active && !state?.paused) {
      await withTimeout(chrome.runtime.sendMessage({ type: "PAUSE_CAPTURE", tabId }), "PAUSE_CAPTURE");
      await refreshState();
      return;
    }
    if (state?.active && state?.paused) {
      await withTimeout(chrome.runtime.sendMessage({ type: "RESUME_CAPTURE", tabId }), "RESUME_CAPTURE");
      await refreshState();
      return;
    }
    // No session exists at all (never started, or the tab was closed and
    // reopened) — starting one from scratch needs the toolbar-icon gesture
    // (see comment above). A quiet text change here was easy to miss and
    // read as "the button doesn't work" — the pulsing accent-colored hint
    // makes the actual required action obvious.
    renderMaybeMarquee(statusDetail, "↑ 브라우저 툴바의 확장 아이콘을 클릭하세요");
    statusDetail.classList.add("hint");
    setTimeout(() => statusDetail.classList.remove("hint"), 4000);
  } catch (err) {
    console.error("[popup] toggle failed:", err);
    renderMaybeMarquee(statusDetail, `error: ${err?.message ?? err}`);
  }
});

// Mirrors backend/config.py's filtering thresholds — kept in sync manually,
// since the extension can't import the Python config. Used only to color
// the bars below (red = "on the silence/hallucination side of the actual
// filter"), not to re-implement the filtering logic itself.
const AUDIO_RMS_SILENCE_FLOOR = 0.006;
const WHISPER_NO_SPEECH_THRESHOLD = 0.6;
const WHISPER_AVG_LOGPROB_THRESHOLD = -1.0;

function clampPct(fraction) {
  return Math.max(0, Math.min(1, fraction)) * 100;
}

// One bar per confidence metric: label, numeric value, and a horizontal
// fill sized against a fixed reference range so values are visually
// comparable across segments at a glance. Fill is red when the value sits
// on the side of the corresponding backend threshold that risks being
// treated as silence/hallucination, green otherwise.
const METRICS = [
  {
    key: "audio_rms",
    label: "rms",
    format: (v) => v.toFixed(4),
    pct: (v) => clampPct(v / 0.05), // ~0.05 treated as "full bar" (well above speech floor)
    ok: (v) => v >= AUDIO_RMS_SILENCE_FLOOR,
  },
  {
    key: "no_speech_prob",
    label: "no_speech",
    format: (v) => v.toFixed(2),
    pct: (v) => clampPct(v),
    ok: (v) => v < WHISPER_NO_SPEECH_THRESHOLD,
  },
  {
    key: "avg_logprob",
    label: "avg_logprob",
    format: (v) => v.toFixed(2),
    pct: (v) => clampPct((v + 2) / 2), // -2 (worst) .. 0 (best) mapped to 0%..100%
    ok: (v) => v > WHISPER_AVG_LOGPROB_THRESHOLD,
  },
];

function renderConfidenceBars(confLine, data) {
  confLine.textContent = "";
  for (const metric of METRICS) {
    const value = data?.[metric.key];
    if (value == null) continue;
    const row = document.createElement("div");
    row.className = "metric-row";
    const label = document.createElement("span");
    label.className = "metric-label";
    label.textContent = `${metric.label} ${metric.format(value)}`;
    const bar = document.createElement("span");
    bar.className = "metric-bar";
    const fill = document.createElement("span");
    fill.className = `metric-fill ${metric.ok(value) ? "metric-ok" : "metric-risk"}`;
    fill.style.width = `${metric.pct(value)}%`;
    bar.appendChild(fill);
    row.appendChild(label);
    row.appendChild(bar);
    confLine.appendChild(row);
  }
}

function renderEvent(data) {
  const { type, text, translation, segment_id: segmentId } = data ?? {};
  if (!segmentId) return;

  if (!transcriptSeen) {
    transcriptSeen = true;
    renderContextSummaryArea();
  }
  // A transcript event means the capture WS is live — retry the title
  // translation if it hasn't landed yet (time-gated inside).
  requestTitleTranslation();

  let entry = segmentEls.get(segmentId);
  if (!entry) {
    const container = document.createElement("div");
    // Pre-existing gap found while wiring the "warmup" gate below: this base
    // class was never actually applied, so every `.segment...` CSS rule in
    // popup.html (padding/rounding/hover, `.flagged` background, and
    // crucially `.segment.is-partial`/`.segment.is-final`'s dim/blur-vs-
    // crisp distinction — the CLAUDE.md-required partial/final visual cue)
    // was silently dead. classList.toggle("is-final"/"flagged"/...) below
    // was still setting those modifier classes correctly; they just never
    // combined with a "segment" base for the compound selectors to match.
    container.className = "segment";
    const jaLine = document.createElement("div");
    const koLine = document.createElement("div");
    const confLine = document.createElement("div");
    jaLine.className = "ja-line";
    koLine.className = "ko-line";
    confLine.className = "conf-line";
    container.appendChild(jaLine);
    container.appendChild(koLine);
    container.appendChild(confLine);
    // Approximation: single-speaker VAD segmentation processes one
    // utterance at a time, so by the time a new segment's first event
    // arrives, every prior segment has already finalized — finalsSeenCount
    // at creation time is therefore "how many sentences finalized before
    // this one started", which is exactly the warm-up window this segment
    // will see once it finalizes.
    const warmup = finalsSeenCount < HISTORY_WARMUP_FINALS;
    container.classList.toggle("warmup", warmup);
    entry = { container, jaLine, koLine, confLine, flagged: false, confidence: null, warmup };
    segmentEls.set(segmentId, entry);
    logEl.appendChild(container);
    pruneOldSegments();

    // Manual mislabel tagging while watching a live capture: click a
    // sentence to mark it wrong (pink), click again to undo. Reads whatever
    // text is currently displayed at click time, so it always reflects the
    // latest partial/final content shown to the user. Relayed through to
    // the backend (offscreen.js -> ws "flag_segment") so it lands in
    // data/flagged_segments.jsonl for later review — see backend/main.py.
    container.addEventListener("click", () => {
      entry.flagged = !entry.flagged;
      container.classList.toggle("flagged", entry.flagged);
      chrome.runtime
        .sendMessage({
          type: "FLAG_SEGMENT",
          tabId,
          segmentId,
          flagged: entry.flagged,
          // The raw text/translation, not entry.jaLine/koLine's rendered
          // textContent — when music_suspected swapped those for a "🎵"
          // placeholder, flagging still needs the real (likely garbled)
          // content for QA review, not the placeholder string.
          text: entry.rawText ?? entry.jaLine.textContent,
          translation: entry.rawTranslation ?? entry.koLine.textContent,
          // Whatever confidence numbers were last shown for this segment
          // (final events only — see below), so flagged_segments.jsonl
          // carries the evidence needed to tune the no_speech_prob
          // thresholds in backend/config.py instead of guessing.
          ...(entry.confidence ?? {}),
        })
        .catch(() => {});
    });
  }

  const stateClass = type === "final" ? "final" : "partial";
  entry.jaLine.className = `ja-line ${stateClass}`;
  entry.koLine.className = `ko-line ${stateClass}`;
  entry.container.classList.toggle("is-final", stateClass === "final");
  entry.container.classList.toggle("is-partial", stateClass === "partial");
  // Music/BGM placeholder (2026-08-25): final-only, backend/music_gate.py's
  // MusicGate.music_suspected() flag — hide the (likely garbled/confidently
  // wrong) transcript+translation rather than show it. The explanatory
  // "🎵 노래·배경음악 감지됨" label itself only renders under the debug
  // toggle (CSS ::before, popup.html) — a normal viewer instead sees the
  // ambient icon in the summary bar (updateMusicIndicator below), not a
  // per-segment text label. Note this heuristic was only validated on
  // synthetic test tones as of 2026-08-25 (see music_gate.py's docstring),
  // so false positives on real short/energetic speech are possible.
  const musicSuspected = type === "final" && data?.music_suspected === true;
  entry.container.classList.toggle("music-suspected", musicSuspected);
  entry.rawText = text ?? "";
  entry.rawTranslation = translation ?? "";
  // 2026-08-25's music/BGM gate is a display-only hint (see audio_session.py) —
  // it must never hide a real transcript/translation the backend actually
  // produced. Music detection turned out to false-positive heavily on normal
  // speech (observed live: ~50-70% of finals flagged), so blanking text here
  // was silently wiping most translations. Always render the real text; the
  // music-suspected class just dims the segment's border and (debug mode
  // only) adds a "🎵" label — see popup.html.
  entry.jaLine.textContent = entry.rawText;
  entry.koLine.textContent = entry.rawTranslation;
  if (type === "final") updateMusicIndicator(musicSuspected);
  // Debugging aid (see CLAUDE.md hallucination-filtering discussion): only
  // "final" events carry Whisper's confidence signals today (audio_session.py
  // doesn't compute them on the fast/partial pass), so this stays blank
  // until a segment finalizes.
  if (type === "final") {
    renderConfidenceBars(entry.confLine, data);
    entry.confidence = {
      audio_rms: data.audio_rms,
      no_speech_prob: data.no_speech_prob,
      avg_logprob: data.avg_logprob,
    };
    if (!countedFinalSegments.has(segmentId)) {
      countedFinalSegments.add(segmentId);
      finalsSeenCount++;
    }
  } else {
    entry.confLine.textContent = "";
  }
}

chrome.runtime.onMessage.addListener((message) => {
  if (message?.type !== "TRANSCRIPT_EVENT") return;
  if (message.tabId !== tabId) return; // Ignore messages from other tabs
  if (restoring) {
    pendingEvents.push(message.data);
    return;
  }
  renderEvent(message.data);
  // Only auto-follow while tracking is engaged — otherwise a live arrival
  // would yank the viewer away from history they deliberately scrolled up
  // to read. updateScrollToBottomButton() surfaces the button instead so
  // they can jump back down (and re-arm tracking) manually when ready.
  if (autoTrack) {
    scrollLogToBottom();
  }
  updateScrollToBottomButton();
});

// Context summary (2026-08-20): one-line "what's being talked about right
// now", regenerated periodically by the backend (config.py's
// CONTEXT_SUMMARY_EVERY_N_FINALS) from recent final JA speech. title=
// mirrors the text so hovering shows the full line even when the header's
// fixed width ellipsis-truncates it.
const contextSummaryEl = document.getElementById("contextSummary");

// Ambient "노래/배경음악 감지" indicator (2026-08-25) — right edge of the
// summary bar, reflects the most recent final's music_suspected flag
// regardless of debug mode (unlike the per-segment placeholder text, which
// is debug-only — see popup.html's .segment.music-suspected rule). Idle/dim
// by default, pulses while active.
const musicIndicatorEl = document.getElementById("musicIndicator");

function updateMusicIndicator(active) {
  musicIndicatorEl.classList.toggle("active", active);
  musicIndicatorEl.title = active ? "노래·배경음악 감지됨" : "노래·배경음악 감지 안 됨";
}

function renderContextSummary(text) {
  contextSummaryText = text ?? "";
  renderContextSummaryArea();
}

// Priority: real backend summary > placeholder > nothing. Placeholder is
// "지금 무슨 내용인지 파악 중이에요." once transcription is flowing,
// "전사/번역 준비 중입니다." while the backend is still starting up (capture
// active but no transcript yet). Idle with no summary shows an empty bar.
function renderContextSummaryArea() {
  let text = "";
  let placeholder = false;
  if (contextSummaryText) {
    text = contextSummaryText;
  } else if (captureActive && transcriptSeen) {
    text = "지금 무슨 내용인지 파악 중이에요.";
    placeholder = true;
  } else if (captureActive) {
    text = "전사/번역 준비 중입니다.";
    placeholder = true;
  }
  contextSummaryEl.classList.toggle("placeholder", placeholder);
  renderMaybeMarquee(contextSummaryEl, text);
}

// Mid-session video switch (2026-08-25) — background.js's updateVideoMetadata
// broadcasts this live so an already-open overlay reflects the new
// channel/title immediately instead of waiting for the next refreshState().
chrome.runtime.onMessage.addListener((message) => {
  if (message?.type !== "VIDEO_META_UPDATED") return;
  if (message.tabId !== tabId) return;
  lastVideoMeta = {
    channelName: message.channelName ?? null,
    channelAvatarUrl: message.channelAvatarUrl ?? null,
    videoTitle: message.videoTitle ?? null,
    streamStartedAt: message.streamStartedAt ?? null,
  };
  renderVideoMeta(lastVideoMeta);
  if (message.videoTitle) {
    lastCaptureTitle = message.videoTitle;
    titleOriginal = message.videoTitle;
    renderHeaderTitle();
    requestTitleTranslation();
  }
});

chrome.runtime.onMessage.addListener((message) => {
  if (message?.type !== "CONTEXT_SUMMARY") return;
  if (message.tabId !== tabId) return; // Ignore messages from other tabs
  renderContextSummary(message.data.text);
});

async function restoreContextSummary() {
  const text = await chrome.runtime.sendMessage({ type: "GET_CONTEXT_SUMMARY", tabId });
  renderContextSummary(text);
}

// Volume meter (2026-08-20): "the pipeline is actually alive" feedback,
// especially useful during silence where no partial/final event fires at
// all. Sent directly from offscreen.js via chrome.runtime broadcast (~5Hz,
// see VOLUME_SEND_INTERVAL_MS there) — no persistence needed, it's a
// pure live signal like CHAT_TRANSLATION. Not restored on reopen (there's
// nothing meaningful to restore; it just starts filling again on the next
// broadcast). Same 0.05 "full bar" reference as the debug rms metric below,
// for a consistent sense of scale across the two.
//
// Raw samples only arrive every ~200ms — snapping the bar's width directly
// to each one looked like a stepped, laggy meter. Instead, every message
// just updates a target, and a requestAnimationFrame loop eases the
// displayed width toward that target on every frame (~60fps): fewer
// messages, but motion reads as continuous/live rather than choppy. The
// loop runs continuously (cheap — one multiply/compare/style-write per
// frame, and rAF itself already pauses while the tab is hidden).
const volumeBarFill = document.getElementById("volumeBarFill");
// volumeTargetPct: the real, level-driven target from the latest message.
// volumeAnimTarget: the waypoint the easing loop below is currently chasing
// — usually equal to volumeTargetPct, but briefly a bounce point beyond it
// (2026-08-30) whenever the level moves: rising energy overshoots up to
// +VOLUME_OVERSHOOT_MAX_PCT past the new target before settling back down to
// it, falling energy undershoots the same amount below it before rising back
// up — a little spring/rubber-band feel instead of a flat ease-in. Direction
// is decided by comparing the newly arrived target against the previous one
// (the actual trend of the signal), not against wherever the bar is
// currently mid-animation.
let volumeTargetPct = 0;
let volumeAnimTarget = 0;
let volumeDisplayedPct = 0;
let volumeBouncing = false;
const VOLUME_OVERSHOOT_MAX_PCT = 3;

function tickVolumeAnimation() {
  volumeDisplayedPct += (volumeAnimTarget - volumeDisplayedPct) * 0.2;
  if (Math.abs(volumeAnimTarget - volumeDisplayedPct) < 0.15) {
    volumeDisplayedPct = volumeAnimTarget;
    if (volumeBouncing) {
      // Reached the overshoot/undershoot waypoint — ease back to the real
      // target next.
      volumeBouncing = false;
      volumeAnimTarget = volumeTargetPct;
    }
  }
  volumeBarFill.style.width = `${volumeDisplayedPct}%`;
  requestAnimationFrame(tickVolumeAnimation);
}
requestAnimationFrame(tickVolumeAnimation);

chrome.runtime.onMessage.addListener((message) => {
  if (message?.type !== "VOLUME_LEVEL") return;
  if (message.tabId !== tabId) return; // Ignore messages from other tabs
  const newTargetPct = clampPct(message.level / 0.05);
  const bounce = Math.random() * VOLUME_OVERSHOOT_MAX_PCT;
  if (newTargetPct > volumeTargetPct) {
    volumeAnimTarget = Math.min(100, newTargetPct + bounce); // never past the bar's own max
    volumeBouncing = true;
  } else if (newTargetPct < volumeTargetPct) {
    volumeAnimTarget = Math.max(0, newTargetPct - bounce);
    volumeBouncing = true;
  } else {
    volumeAnimTarget = newTargetPct;
    volumeBouncing = false;
  }
  volumeTargetPct = newTargetPct;
});

// This iframe's own DOM/state is destroyed every time the overlay panel is
// closed (the ✕ button in content_script.js removes the whole panel,
// iframe included), but capture keeps running independently in the
// background/offscreen contexts. Restore whatever history accumulated while
// this iframe instance didn't exist, from the persisted log in
// background.js, before subscribing to new live events above.
async function restoreHistory() {
  const log = await chrome.runtime.sendMessage({ type: "GET_TRANSCRIPT_LOG", tabId });
  for (const event of log ?? []) {
    renderEvent(event);
  }
  restoring = false;
  for (const event of pendingEvents) {
    renderEvent(event);
  }
  pendingEvents.length = 0;
  scrollLogToBottom();
  updateScrollToBottomButton();
}

restoreHistory();
refreshState();
restoreContextSummary();

// "방송 시작 X분 전" is computed client-side from an absolute timestamp, so
// it drifts stale without a periodic re-render — no new round-trip needed,
// just recompute from the already-cached lastVideoMeta.
setInterval(() => renderVideoMeta(lastVideoMeta), 30000);

// --- Chat reply (draft, 2026-08-20): translate the viewer's own Korean ---
// --- chat message into Japanese, using the live broadcast as context. ---
const chatInputEl = document.getElementById("chatInput");
const chatTranslateBtn = document.getElementById("chatTranslateBtn");
const chatOutputEl = document.getElementById("chatOutput");
const chatStatusEl = document.getElementById("chatStatus");

let pendingChatRequestId = null;

// Hidden context-injection commands (2026-08-29, debug metrics only —
// see popup.html's debugToggle): typed into the same chat-reply box above,
// these don't translate at all — they steer the running context summary
// (contextSummaryEl / backend/audio_session.py's _current_summary) instead.
// Gated on debug mode so an ordinary chat message that happens to start
// with "(summary)" or read exactly "(init)" is never swallowed for a normal
// viewer who has no reason to know this syntax exists. The tag is checked
// FIRST, before anything resembling a translate request is sent — matching
// either form must never fall through to TRANSLATE_CHAT below.
//   (summary)텍스트     -> inject "텍스트" as a note the summary must
//   (summary)[텍스트]      account for, and regenerate the summary right
//                          away. Brackets are accepted but optional — a
//                          bare "(summary)텍스트" works the same way.
//   (init)              -> drop all accumulated context and restart the
//                          summary from just the current video title.
const SUMMARY_BRACKET_RE = /^\(summary\)\s*\[([\s\S]*)\]$/;
const SUMMARY_PLAIN_RE = /^\(summary\)\s*([\s\S]+)$/;

async function sendChatTranslateRequest() {
  const raw = chatInputEl.value.trim();
  if (!raw) return;

  if (document.getElementById("debugToggle").checked) {
    const summaryMatch = raw.match(SUMMARY_BRACKET_RE) ?? raw.match(SUMMARY_PLAIN_RE);
    if (summaryMatch || raw === "(init)") {
      chatInputEl.value = "";
      chatOutputEl.textContent = "";
      chatOutputEl.classList.remove("copied");
      // Confirmation only — no translation happens for either tag, and no
      // "in progress" -> "done" two-step; the injection/reset itself is
      // fire-and-forget from the UI's perspective.
      chatStatusEl.textContent = summaryMatch ? "요약 참고할게요!" : "맥락 초기화할게요!";
      chrome.runtime
        .sendMessage(
          summaryMatch
            ? { type: "INJECT_CONTEXT_SUMMARY", tabId, text: summaryMatch[1] }
            : { type: "RESET_CONTEXT_SUMMARY", tabId }
        )
        .catch((err) => {
          console.error("[popup] context-injection command failed:", err);
          chatStatusEl.textContent = `error: ${err?.message ?? err}`;
        });
      return;
    }
  }

  const text = raw;
  // One in flight at a time — a second click before the first reply lands
  // just supersedes it (pendingChatRequestId check below drops the stale
  // reply rather than showing an out-of-order result).
  const requestId = `${Date.now()}-${Math.random().toString(36).slice(2)}`;
  pendingChatRequestId = requestId;
  chatTranslateBtn.disabled = true;
  chatStatusEl.textContent = "번역 중...";
  chatOutputEl.textContent = "";
  chatOutputEl.classList.remove("copied");
  try {
    await chrome.runtime.sendMessage({ type: "TRANSLATE_CHAT", tabId, text, requestId });
  } catch (err) {
    console.error("[popup] TRANSLATE_CHAT send failed:", err);
    chatStatusEl.textContent = `error: ${err?.message ?? err}`;
    chatTranslateBtn.disabled = false;
  }
}

chatTranslateBtn.addEventListener("click", sendChatTranslateRequest);
chatInputEl.addEventListener("keydown", (e) => {
  // Enter sends, Shift+Enter for a newline (matches common chat-box convention).
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    sendChatTranslateRequest();
  }
});

// Click the translated output to copy it — the whole point is pasting into
// the stream's own chat box, which this extension has no reliable way to
// reach directly (see popup.html's UI-scoping discussion, 2026-08-20).
chatOutputEl.addEventListener("click", async () => {
  if (!chatOutputEl.textContent) return;
  try {
    await navigator.clipboard.writeText(chatOutputEl.textContent);
    chatOutputEl.classList.add("copied");
    chatStatusEl.textContent = "복사됨";
  } catch (err) {
    console.error("[popup] clipboard write failed, falling back to execCommand:", err);
    // navigator.clipboard needs the embedding page to delegate the
    // clipboard-write Permissions-Policy to this iframe (content_script.js
    // sets iframe allow="clipboard-write") — if that's ever missing or the
    // browser rejects it anyway, select-and-execCommand still works since it
    // doesn't go through the async Clipboard API's permission check.
    try {
      const range = document.createRange();
      range.selectNodeContents(chatOutputEl);
      const selection = window.getSelection();
      selection.removeAllRanges();
      selection.addRange(range);
      const ok = document.execCommand("copy");
      selection.removeAllRanges();
      if (!ok) throw new Error("execCommand returned false");
      chatOutputEl.classList.add("copied");
      chatStatusEl.textContent = "복사됨";
    } catch (fallbackErr) {
      console.error("[popup] execCommand copy fallback also failed:", fallbackErr);
      chatStatusEl.textContent = "복사 실패 — 직접 선택해서 복사하세요";
    }
  }
});

chrome.runtime.onMessage.addListener((message) => {
  if (message?.type !== "CHAT_TRANSLATION") return;
  if (message.tabId !== tabId) return; // Ignore messages from other tabs
  const { request_id: requestId, translation } = message.data ?? {};
  chatTranslateBtn.disabled = false;
  if (requestId !== pendingChatRequestId) return; // superseded by a newer request
  chatOutputEl.textContent = translation || "(번역 결과 없음)";
  chatStatusEl.textContent = translation ? "클릭해서 복사" : "";
});

// Debug toggle
document.getElementById("debugToggle").addEventListener("change", (e) => {
  document.body.classList.toggle("show-debug", e.target.checked);
  // Toggling reveals/hides confidence bars and warmup segments (see
  // popup.html's .show-debug rules), which can grow/shrink logEl's content
  // height with no "scroll" event and no logEl box-size change (so the
  // ResizeObserver above doesn't fire either — it watches logEl's own
  // border box, not its scrollHeight). If tracking was engaged, follow the
  // shifted bottom instead of leaving the view stranded mid-log; reading
  // scrollHeight here is already after the class change applied, forcing
  // the layout to settle so it reflects the new (post-toggle) height.
  if (autoTrack) scrollLogToBottom();
  updateScrollToBottomButton();
});

// Theme toggle (manual override on top of the prefers-color-scheme default —
// see popup.html's :root[data-theme] blocks). Stored in localStorage, which
// is scoped to this extension's own origin (chrome-extension://<id>) rather
// than to any one YouTube tab, so the choice is shared across every tab's
// overlay panel automatically instead of needing per-tab bookkeeping.
const THEME_STORAGE_KEY = "lt_theme";
const themeToggleBtn = document.getElementById("themeToggle");
const darkMql = window.matchMedia("(prefers-color-scheme: dark)");

function effectiveTheme() {
  return document.documentElement.getAttribute("data-theme") ?? (darkMql.matches ? "dark" : "light");
}

function syncThemeToggleIcon() {
  const dark = effectiveTheme() === "dark";
  themeToggleBtn.textContent = dark ? "🌙" : "☀️";
  themeToggleBtn.setAttribute("aria-label", dark ? "라이트 모드로 전환" : "다크 모드로 전환");
}

const savedTheme = localStorage.getItem(THEME_STORAGE_KEY);
if (savedTheme === "light" || savedTheme === "dark") {
  document.documentElement.setAttribute("data-theme", savedTheme);
}
syncThemeToggleIcon();

themeToggleBtn.addEventListener("click", () => {
  const next = effectiveTheme() === "dark" ? "light" : "dark";
  document.documentElement.setAttribute("data-theme", next);
  localStorage.setItem(THEME_STORAGE_KEY, next);
  syncThemeToggleIcon();
});

// Once the user has made an explicit choice, stop tracking OS changes (that
// choice is meant to stick). Only re-sync live with the OS while still on
// the default "auto" behavior (no stored preference).
darkMql.addEventListener("change", () => {
  if (!localStorage.getItem(THEME_STORAGE_KEY)) syncThemeToggleIcon();
});
