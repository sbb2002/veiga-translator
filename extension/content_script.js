// content_script.js — in-page overlay panel (2026-08-20).
//
// Replaces the earlier chrome.windows.create detached viewer window: a
// separate OS window loses focus (and visually drops behind) the instant the
// user clicks the YouTube tab itself, which the user flagged as the actual
// blocker to using this day-to-day. An overlay layered directly into the
// captured page's own DOM has no OS-level focus to lose — it just always
// paints on top via z-index, same as any other floating in-page widget.
//
// Reuses popup.html/popup.js completely unchanged, loaded in an <iframe>
// (declared in manifest.json web_accessible_resources) — that file already
// talks to background.js purely over chrome.runtime messaging keyed by
// ?tabId=, which works identically whether it's a standalone window or an
// iframe embedded in a normal page. This script only owns the floating
// chrome around that iframe: position, drag, resize, close.
//
// One tab = one independent overlay. This file runs once per matched tab
// (content scripts are per-frame/per-tab), so tabId below is always this
// tab's own id — nothing here ever needs to reason about other tabs.

(() => {
  if (window.__liveTranslatorOverlayInit) return; // idempotent re-injection guard
  window.__liveTranslatorOverlayInit = true;

  const PANEL_WIDTH = 380;
  const PANEL_HEIGHT = 560;
  const HEADER_HEIGHT = 30;
  const MIN_WIDTH = 300;
  const MIN_HEIGHT = 220;

  let rootEl = null;
  let iframeEl = null;
  let currentTabId = null;

  function buildPanel(tabId) {
    if (rootEl) return; // already showing — SHOW_OVERLAY is otherwise idempotent

    currentTabId = tabId;

    const root = document.createElement("div");
    root.id = "live-translator-overlay-root";
    // left/top from the start (never "right") — resize below grows/shrinks
    // by adjusting width/height while left+top stay fixed, which only
    // tracks the mouse correctly if left is already the real anchor. Also
    // clamped so a narrow window doesn't place the panel off-screen.
    const initialLeft = Math.max(16, window.innerWidth - PANEL_WIDTH - 16);
    Object.assign(root.style, {
      position: "fixed",
      top: "72px",
      left: `${initialLeft}px`,
      width: `${PANEL_WIDTH}px`,
      height: `${PANEL_HEIGHT}px`,
      zIndex: "2147483647",
      borderRadius: "10px",
      overflow: "hidden",
      boxShadow: "0 8px 28px rgba(0,0,0,0.45)",
      background: "#1c2029",
      display: "flex",
      flexDirection: "column",
      fontFamily:
        '-apple-system, BlinkMacSystemFont, "Segoe UI", "Malgun Gothic", sans-serif',
    });

    const header = document.createElement("div");
    Object.assign(header.style, {
      flex: "none",
      height: `${HEADER_HEIGHT}px`,
      display: "flex",
      alignItems: "center",
      justifyContent: "space-between",
      padding: "0 8px 0 10px",
      background: "#14171c",
      color: "#8b93a3",
      fontSize: "11px",
      fontWeight: "600",
      cursor: "move",
      userSelect: "none",
    });
    const title = document.createElement("span");
    title.textContent = "Live Translator";

    const headerButtons = document.createElement("span");
    Object.assign(headerButtons.style, { display: "flex", alignItems: "center", gap: "2px" });

    const minimizeBtn = document.createElement("button");
    minimizeBtn.textContent = "─";
    minimizeBtn.title = "패널 최소화 (캡처는 계속됨)";
    Object.assign(minimizeBtn.style, {
      border: "0",
      background: "transparent",
      color: "#8b93a3",
      fontSize: "13px",
      cursor: "pointer",
      padding: "4px 6px",
      lineHeight: "1",
    });
    minimizeBtn.addEventListener("mouseenter", () => (minimizeBtn.style.color = "#eef0f3"));
    minimizeBtn.addEventListener("mouseleave", () => (minimizeBtn.style.color = "#8b93a3"));

    const closeBtn = document.createElement("button");
    closeBtn.textContent = "✕";
    closeBtn.title = "캡처 완전 종료 후 패널 닫기";
    Object.assign(closeBtn.style, {
      border: "0",
      background: "transparent",
      color: "#8b93a3",
      fontSize: "13px",
      cursor: "pointer",
      padding: "4px 6px",
      lineHeight: "1",
    });
    closeBtn.addEventListener("mouseenter", () => (closeBtn.style.color = "#eef0f3"));
    closeBtn.addEventListener("mouseleave", () => (closeBtn.style.color = "#8b93a3"));
    // ✕ now fully ends the session (2026-08-20) rather than just hiding the
    // panel — the toggle button inside popup.js already covers "keep the
    // session alive but stop listening" (pause), so ✕'s job is specifically
    // the other case: actually tear down the tabCapture stream/websocket.
    // STOP_CAPTURE needs no privileged gesture (it's a teardown, not an
    // acquire), so this is safe to fire from the overlay itself — same
    // reasoning as pause/resume in background.js.
    closeBtn.addEventListener("click", () => {
      chrome.runtime.sendMessage({ type: "STOP_CAPTURE", tabId }).catch(() => {});
      removePanel();
    });
    header.appendChild(title);
    headerButtons.appendChild(minimizeBtn);
    headerButtons.appendChild(closeBtn);
    header.appendChild(headerButtons);

    const iframe = document.createElement("iframe");
    iframe.src = chrome.runtime.getURL(`popup.html?tabId=${tabId}`);
    // Clipboard access (popup.js's click-to-copy chat translation) is a
    // Permissions-Policy feature: an embedding page must explicitly delegate
    // it to a cross-origin iframe, or navigator.clipboard.writeText() silently
    // rejects even though the exact same code worked fine in the old
    // standalone popup window (which had no embedder to withhold it).
    iframe.setAttribute("allow", "clipboard-write");
    Object.assign(iframe.style, {
      flex: "1",
      minHeight: "0",
      width: "100%",
      border: "0",
      display: "block",
    });

    root.style.position = "fixed"; // (kept explicit; resize handles need root to stay the positioned ancestor)
    root.appendChild(header);
    root.appendChild(iframe);
    // Eight handles: right/left edges (width only), bottom/top edges (height
    // only), and all four corners (both axes at once, diagonal resize) —
    // plain horizontal/vertical resize from either side, plus a diagonal
    // grip at every corner (2026-08-30: only the bottom-right corner existed
    // before — the other three edges could each resize one axis, but no
    // handle let a diagonal drag from top-left/top-right/bottom-left resize
    // both axes together the way the bottom-right corner already did).
    const edgeRight = makeResizeHandle({ right: "0", top: "0", bottom: "0", width: "6px", cursor: "ew-resize" });
    const edgeLeft = makeResizeHandle({ left: "0", top: "0", bottom: "0", width: "6px", cursor: "ew-resize" });
    const edgeBottom = makeResizeHandle({ left: "0", right: "0", bottom: "0", height: "6px", cursor: "ns-resize" });
    const edgeTop = makeResizeHandle({ left: "0", right: "0", top: "0", height: "6px", cursor: "ns-resize" });
    // nwse-resize: the NW<->SE diagonal (top-left / bottom-right corners).
    // nesw-resize: the NE<->SW diagonal (top-right / bottom-left corners).
    const cornerBR = makeResizeHandle({ right: "0", bottom: "0", width: "16px", height: "16px", cursor: "nwse-resize" });
    const cornerBL = makeResizeHandle({ left: "0", bottom: "0", width: "16px", height: "16px", cursor: "nesw-resize" });
    const cornerTR = makeResizeHandle({ right: "0", top: "0", width: "16px", height: "16px", cursor: "nesw-resize" });
    const cornerTL = makeResizeHandle({ left: "0", top: "0", width: "16px", height: "16px", cursor: "nwse-resize" });
    root.appendChild(edgeRight);
    root.appendChild(edgeLeft);
    root.appendChild(edgeBottom);
    root.appendChild(edgeTop);
    root.appendChild(cornerBR);
    root.appendChild(cornerBL);
    root.appendChild(cornerTR);
    root.appendChild(cornerTL);
    document.documentElement.appendChild(root);

    rootEl = root;
    iframeEl = iframe;

    makeDraggable(header, root);
    // x/y: which fixed edge each handle grows away from — 'right'/'bottom'
    // keep left/top fixed (matches the panel's left/top anchor from
    // creation); 'left'/'top' keep the opposite edge fixed instead, moving
    // left/top themselves as the handle side is dragged — see makeResizable.
    makeResizable(edgeRight, root, { x: "right", y: null });
    makeResizable(edgeLeft, root, { x: "left", y: null });
    makeResizable(edgeBottom, root, { x: null, y: "bottom" });
    makeResizable(edgeTop, root, { x: null, y: "top" });
    makeResizable(cornerBR, root, { x: "right", y: "bottom" });
    makeResizable(cornerBL, root, { x: "left", y: "bottom" });
    makeResizable(cornerTR, root, { x: "right", y: "top" });
    makeResizable(cornerTL, root, { x: "left", y: "top" });
    followFullscreen(root);

    // Minimize (2026-08-20): collapses the panel down to just the header
    // bar — capture keeps running untouched (unlike ✕, this sends no
    // message to background.js at all; it's purely a local display change).
    // Distinct from ✕: this is for "get it out of the way for a moment",
    // ✕ is for "I'm done, stop capturing".
    const resizeHandles = [edgeRight, edgeLeft, edgeBottom, edgeTop, cornerBR, cornerBL, cornerTR, cornerTL];
    let minimized = false;
    let expandedHeight = root.style.height;
    minimizeBtn.addEventListener("click", () => {
      minimized = !minimized;
      if (minimized) {
        expandedHeight = root.style.height;
        root.style.height = `${HEADER_HEIGHT}px`;
        iframe.style.display = "none";
        resizeHandles.forEach((h) => (h.style.display = "none"));
        minimizeBtn.textContent = "▢";
        minimizeBtn.title = "패널 펼치기";
      } else {
        root.style.height = expandedHeight;
        iframe.style.display = "block";
        resizeHandles.forEach((h) => (h.style.display = ""));
        minimizeBtn.textContent = "─";
        minimizeBtn.title = "패널 최소화 (캡처는 계속됨)";
      }
    });
  }

  function makeResizeHandle(edgeStyle) {
    const handle = document.createElement("div");
    Object.assign(handle.style, { position: "absolute", zIndex: "1", ...edgeStyle });
    return handle;
  }

  function removePanel() {
    if (!rootEl) return;
    rootEl.remove();
    rootEl = null;
    iframeEl = null;
  }

  // Dragging moves the root by absolute left/top (already the anchor set at
  // creation — see buildPanel). Uses Pointer Events + setPointerCapture
  // rather than mousemove/mouseup on `document` with a manual
  // iframe.style.pointerEvents toggle: the toggle approach was unreliable
  // live (drag felt inert, then the panel would suddenly jump on the next
  // plain mouse-over once the button was already released, and only a fresh
  // click would "unstick" it) — a race between the style write landing and
  // the pointer already being over the iframe, and no captured target meant
  // events could get eaten by page/iframe handlers before ever reaching our
  // document-level listener. setPointerCapture on the handle itself routes
  // every subsequent pointermove/pointerup straight to that handle
  // regardless of what's visually underneath the cursor, so the iframe
  // never gets a chance to intercept anything and no toggle is needed.
  function makeDraggable(handle, root) {
    handle.addEventListener("pointerdown", (downEvent) => {
      // The close button lives inside this header — without this guard,
      // its pointerdown bubbles up here first, and setPointerCapture below
      // then claims the pointer for dragging before the button ever gets a
      // "click", making it look completely unresponsive.
      if (downEvent.target.closest("button")) return;
      downEvent.preventDefault();
      handle.setPointerCapture(downEvent.pointerId);
      const rect = root.getBoundingClientRect();

      const startX = downEvent.clientX;
      const startY = downEvent.clientY;
      const startLeft = rect.left;
      const startTop = rect.top;

      function onMove(moveEvent) {
        const dx = moveEvent.clientX - startX;
        const dy = moveEvent.clientY - startY;
        root.style.left = `${Math.max(0, startLeft + dx)}px`;
        root.style.top = `${Math.max(0, startTop + dy)}px`;
      }
      function onUp(upEvent) {
        handle.releasePointerCapture(upEvent.pointerId);
        handle.removeEventListener("pointermove", onMove);
        handle.removeEventListener("pointerup", onUp);
      }
      handle.addEventListener("pointermove", onMove);
      handle.addEventListener("pointerup", onUp);
    });
  }

  // `anchor.x`/`anchor.y` say which side is fixed while this handle resizes
  // that axis: 'right'/'bottom' keep left/top fixed and grow the
  // width/height directly with the mouse delta (matches the panel's
  // left/top-anchored position from creation — see buildPanel); 'left'/'top'
  // instead keep the *opposite* edge fixed and compute width/height from
  // the distance to the mouse, moving left/top to match so the fixed edge
  // really does stay put. null means this handle doesn't touch that axis.
  // See makeDraggable's comment above for why this uses pointer capture
  // instead of document-level mousemove/mouseup.
  function makeResizable(grip, root, anchor) {
    grip.addEventListener("pointerdown", (downEvent) => {
      downEvent.preventDefault();
      downEvent.stopPropagation();
      grip.setPointerCapture(downEvent.pointerId);
      const rect = root.getBoundingClientRect();
      const startX = downEvent.clientX;
      const startY = downEvent.clientY;
      const startWidth = rect.width;
      const startHeight = rect.height;
      const fixedRight = rect.left + rect.width; // anchor for 'left'-edge resize
      const fixedBottom = rect.top + rect.height; // anchor for 'top'-edge resize

      function onMove(moveEvent) {
        if (anchor.x === "right") {
          const dx = moveEvent.clientX - startX;
          root.style.width = `${Math.max(MIN_WIDTH, startWidth + dx)}px`;
        } else if (anchor.x === "left") {
          const newWidth = Math.max(MIN_WIDTH, fixedRight - moveEvent.clientX);
          root.style.width = `${newWidth}px`;
          root.style.left = `${fixedRight - newWidth}px`;
        }
        if (anchor.y === "bottom") {
          const dy = moveEvent.clientY - startY;
          root.style.height = `${Math.max(MIN_HEIGHT, startHeight + dy)}px`;
        } else if (anchor.y === "top") {
          const newHeight = Math.max(MIN_HEIGHT, fixedBottom - moveEvent.clientY);
          root.style.height = `${newHeight}px`;
          root.style.top = `${fixedBottom - newHeight}px`;
        }
      }
      function onUp(upEvent) {
        grip.releasePointerCapture(upEvent.pointerId);
        grip.removeEventListener("pointermove", onMove);
        grip.removeEventListener("pointerup", onUp);
      }
      grip.addEventListener("pointermove", onMove);
      grip.addEventListener("pointerup", onUp);
    });
  }

  // YouTube's native fullscreen (both the player's own fullscreen button and
  // the browser's) makes only the fullscreen element and its descendants
  // paintable — a panel left under <html> would silently vanish. Re-parent
  // into whatever becomes the fullscreen element, and back to <html> when
  // fullscreen ends, so the panel keeps showing either way.
  function followFullscreen(root) {
    document.addEventListener("fullscreenchange", () => {
      if (!rootEl) return;
      const target = document.fullscreenElement || document.documentElement;
      target.appendChild(rootEl);
    });
  }

  // Video/channel metadata scrape (2026-08-25): who's actually streaming plus
  // the video's own title/description/live-start time, so the backend can
  // (a) log richer session metadata and (b) use the channel name as a
  // translation hint for self-reference. Run at capture start AND on every
  // in-page navigation (maybeReportMetadataChange below).
  //
  // Source priority (2026-08-29 rework): the live DOM + URL first. YouTube's
  // ytInitialPlayerResponse / ytInitialData <script> blobs are the page's own
  // data contract and nice and stable in shape, BUT they are frozen at the
  // initial page load and NOT rewritten when YouTube swaps videos client-
  // side — so after an SPA navigation they still describe the first video.
  // The rendered DOM (title h1, #channel-name link, #avatar img, ?v= param)
  // does follow the current video, so it leads; the frozen blob is consulted
  // only for fields the DOM doesn't expose (streamStartedAt / isLive /
  // description) and only when its videoId still matches the current URL.
  function getChannelAvatarUrl() {
    // DOM first: it reflects the CURRENT video (updates on SPA navigation),
    // and by the time we re-scrape after a nav the owner-avatar <img> already
    // has a real src.
    for (const sel of [
      "#owner #avatar img",
      "ytd-video-owner-renderer #avatar img",
      "#avatar-btn img",
    ]) {
      const src = document.querySelector(sel)?.src;
      if (src && src.startsWith("http")) return src;
    }
    // ytInitialData <script>: inlined in the initial HTML and FROZEN there —
    // never rewritten on in-page navigation. So this is the cold-load path
    // only (on a fresh load the <img> src above is still lazy/empty); after
    // an SPA nav it holds the first video's data and must not be trusted.
    try {
      for (const script of document.querySelectorAll("script")) {
        const text = script.textContent;
        if (!text || !text.includes('"videoOwnerRenderer"')) continue;
        const match = text.match(/ytInitialData\s*=\s*(\{.+\})\s*;/s);
        if (!match) continue;
        const data = JSON.parse(match[1]);
        const items =
          data?.contents?.twoColumnWatchNextResults?.results?.results?.contents ?? [];
        for (const item of items) {
          const thumbs =
            item?.videoSecondaryInfoRenderer?.owner?.videoOwnerRenderer?.thumbnail
              ?.thumbnails;
          if (thumbs?.length) return thumbs[thumbs.length - 1].url;
        }
      }
    } catch (err) {
      console.warn("[content_script] ytInitialData avatar parse failed", err);
    }
    return null;
  }

  let _frozenPR; // parsed once — the ytInitialPlayerResponse <script> never changes after load
  function getFrozenPlayerResponse() {
    if (_frozenPR !== undefined) return _frozenPR;
    _frozenPR = null;
    try {
      for (const script of document.querySelectorAll("script")) {
        const text = script.textContent;
        if (!text || !text.includes("ytInitialPlayerResponse")) continue;
        const match = text.match(/ytInitialPlayerResponse\s*=\s*(\{.*?\});/s);
        if (!match) continue;
        const data = JSON.parse(match[1]);
        if (data?.videoDetails?.videoId) {
          _frozenPR = data;
          break;
        }
      }
    } catch (err) {
      console.warn("[content_script] ytInitialPlayerResponse parse failed", err);
    }
    return _frozenPR;
  }

  function scrapeVideoMetadata() {
    const videoId = new URLSearchParams(location.search).get("v") || null;
    const channelAvatarUrl = getChannelAvatarUrl();

    // Live DOM / URL — these track the CURRENT video across in-page (SPA)
    // navigation. The ytInitialPlayerResponse <script> below does NOT: it is
    // frozen at the initial page load and never rewritten when YouTube swaps
    // videos client-side, so relying on it made the overlay keep showing the
    // first video's title/channel/avatar forever (2026-08-29).
    const domTitle = (
      document.querySelector("ytd-watch-metadata h1, h1.ytd-watch-metadata")?.textContent ||
      document.title.replace(/\s*-\s*YouTube$/, "")
    )
      .replace(/^\(\d+\)\s*/, "")
      .trim();
    const domChannel =
      document
        .querySelector(
          "ytd-watch-metadata #channel-name a, #owner #channel-name a, ytd-video-owner-renderer ytd-channel-name a"
        )
        ?.textContent?.trim() || null;

    // ytInitialPlayerResponse — parsed once and cached (the <script> is frozen
    // at page load, so re-parsing on every nav re-check is wasted work). Only
    // trusted when its own videoId still matches the current URL (no SPA nav
    // since load) — it's the only source for streamStartedAt/isLive/description.
    const frozen = getFrozenPlayerResponse();
    const vd = frozen && frozen.videoDetails.videoId === videoId ? frozen.videoDetails : null;
    const lbd = vd
      ? frozen.microformat?.playerMicroformatRenderer?.liveBroadcastDetails
      : null;

    const channelName = domChannel || vd?.author || null;
    const videoTitle = domTitle || vd?.title || null;

    return {
      channelName,
      channelAvatarUrl,
      url: location.href,
      videoTitle,
      videoId,
      isLive: vd?.isLiveContent ?? null,
      // ISO 8601 absolute time — precise and locale-independent, unlike the
      // rendered "streaming started Xm ago" badge text.
      streamStartedAt: lbd?.startTimestamp ?? null,
      description: vd ? (vd.shortDescription || "").slice(0, 1000) : null,
      source: domChannel || domTitle ? "dom" : vd ? "ytInitialPlayerResponse" : "none",
    };
  }

  // Re-scrape on in-page navigation: YouTube is an SPA, so switching to the
  // next video in the same tab never reloads this content script. YouTube
  // fires 'yt-navigate-finish' on `document` after the route change — but the
  // metadata DOM (title h1, #channel-name link, #avatar img) often lags that
  // event by a beat, and the URL's ?v= flips first. So dedup on the full
  // content signature (not just videoId — a videoId-only key let a stale-DOM
  // first read "claim" the new id and suppress the fresh read that followed),
  // and re-check a few times after each nav to catch the DOM once it settles.
  let lastMetaSig = null;

  function metaSig(m) {
    return [m.videoId, m.channelName, m.videoTitle, m.channelAvatarUrl].join("");
  }

  function maybeReportMetadataChange() {
    const metadata = scrapeVideoMetadata();
    // Require a channel name — a transient nav to a non-video page, or a
    // half-rendered DOM mid-navigation, shouldn't clobber known-good state.
    if (!metadata.channelName) return;
    const sig = metaSig(metadata);
    if (sig === lastMetaSig) return;
    lastMetaSig = sig;
    chrome.runtime.sendMessage({ type: "VIDEO_METADATA_UPDATED", metadata }).catch(() => {});
  }

  // yt-navigate-finish marks the route change; yt-page-data-updated fires when
  // YouTube's page-data store refreshes (often the moment the new metadata is
  // actually ready). Listen to both, debounced, plus a few delayed re-checks
  // after a real navigation to catch the metadata DOM once it settles — the
  // signature dedup in maybeReportMetadataChange makes the extra calls free.
  let _navCheckTimer = null;
  function scheduleMetaCheck() {
    clearTimeout(_navCheckTimer);
    _navCheckTimer = setTimeout(maybeReportMetadataChange, 120);
  }
  document.addEventListener("yt-page-data-updated", scheduleMetaCheck);
  document.addEventListener("yt-navigate-finish", () => {
    scheduleMetaCheck();
    for (const ms of [500, 1500, 3500]) setTimeout(maybeReportMetadataChange, ms);
  });

  chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
    if (message?.type === "SHOW_OVERLAY" && message.tabId != null) {
      buildPanel(message.tabId);
    } else if (message?.type === "SCRAPE_METADATA") {
      const metadata = scrapeVideoMetadata();
      lastMetaSig = metaSig(metadata);
      sendResponse(metadata);
    }
  });
})();
