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
    // Five handles: right/left edges (width only), bottom/top edges (height
    // only), and the bottom-right corner (both) — plain horizontal/vertical
    // resize from either side, plus one diagonal corner.
    const edgeRight = makeResizeHandle({ right: "0", top: "0", bottom: "0", width: "6px", cursor: "ew-resize" });
    const edgeLeft = makeResizeHandle({ left: "0", top: "0", bottom: "0", width: "6px", cursor: "ew-resize" });
    const edgeBottom = makeResizeHandle({ left: "0", right: "0", bottom: "0", height: "6px", cursor: "ns-resize" });
    const edgeTop = makeResizeHandle({ left: "0", right: "0", top: "0", height: "6px", cursor: "ns-resize" });
    const corner = makeResizeHandle({ right: "0", bottom: "0", width: "16px", height: "16px", cursor: "nwse-resize" });
    root.appendChild(edgeRight);
    root.appendChild(edgeLeft);
    root.appendChild(edgeBottom);
    root.appendChild(edgeTop);
    root.appendChild(corner);
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
    makeResizable(corner, root, { x: "right", y: "bottom" });
    followFullscreen(root);

    // Minimize (2026-08-20): collapses the panel down to just the header
    // bar — capture keeps running untouched (unlike ✕, this sends no
    // message to background.js at all; it's purely a local display change).
    // Distinct from ✕: this is for "get it out of the way for a moment",
    // ✕ is for "I'm done, stop capturing".
    const resizeHandles = [edgeRight, edgeLeft, edgeBottom, edgeTop, corner];
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

  // Video/channel metadata scrape (2026-08-25): who's actually streaming
  // plus the video's own title/description/live-start time, so the backend
  // can (a) log richer session metadata and (b) use the channel name as a
  // translation hint for self-reference (see IMPROVEMENT_BACKLOG.md). One-
  // shot at capture start, requested by background.js — no re-scrape on
  // in-page navigation yet (deliberately deferred, see backlog note).
  //
  // Primary source: ytInitialPlayerResponse, a JSON blob YouTube's own
  // player embeds in a <script> tag on every watch page. Preferred over any
  // CSS selector because it's the page's own internal data contract (Google
  // has much more reason to keep this shape stable than any particular
  // rendered DOM structure, which changes with every UI redesign) — verified
  // live 2026-08-25 against an actual stream; the DOM-selector fallback
  // below, by contrast, did NOT match anything on that same page, so treat
  // it as a last resort only.
  function scrapeVideoMetadata() {
    try {
      for (const script of document.querySelectorAll("script")) {
        const text = script.textContent;
        if (!text || !text.includes("ytInitialPlayerResponse")) continue;
        const match = text.match(/ytInitialPlayerResponse\s*=\s*(\{.*?\});/s);
        if (!match) continue;
        const data = JSON.parse(match[1]);
        const vd = data?.videoDetails;
        if (!vd?.author) continue;
        const liveBroadcastDetails =
          data?.microformat?.playerMicroformatRenderer?.liveBroadcastDetails;
        return {
          channelName: vd.author,
          url: location.href,
          videoTitle: vd.title ?? null,
          videoId: vd.videoId ?? null,
          isLive: vd.isLiveContent ?? null,
          // ISO 8601 absolute time — precise and locale-independent, unlike
          // the rendered "streaming started Xm ago" badge text (whose DOM
          // location we couldn't even find a stable selector for).
          streamStartedAt: liveBroadcastDetails?.startTimestamp ?? null,
          description: (vd.shortDescription || "").slice(0, 1000),
          source: "ytInitialPlayerResponse",
        };
      }
    } catch (err) {
      console.warn("[content_script] ytInitialPlayerResponse parse failed", err);
    }

    // Fallback: DOM selectors — brittle to YouTube markup changes, last resort only.
    const selectors = [
      "ytd-watch-metadata #channel-name a",
      "#owner #channel-name a",
      "ytd-video-owner-renderer ytd-channel-name a",
    ];
    for (const sel of selectors) {
      const text = document.querySelector(sel)?.textContent?.trim();
      if (text) {
        return {
          channelName: text,
          url: location.href,
          videoTitle: document.title.replace(/\s*-\s*YouTube$/, ""),
          videoId: null,
          isLive: null,
          streamStartedAt: null,
          description: null,
          source: "dom",
        };
      }
    }
    return {
      channelName: null,
      videoTitle: null,
      videoId: null,
      isLive: null,
      streamStartedAt: null,
      description: null,
      source: "none",
    };
  }

  // Re-scrape on in-page navigation (2026-08-25): YouTube is an SPA, so
  // switching to the next video/live in the same tab never reloads this
  // content script — the one-shot scrape at capture start would otherwise
  // keep reporting the *previous* video's channel/title forever (deferred
  // at the time metadata scraping was first built; now addressed). YouTube
  // fires 'yt-navigate-finish' on `document` after its client-side route
  // change completes (the player's own data, e.g. ytInitialPlayerResponse,
  // is guaranteed updated by then — unlike a generic mutation observer
  // racing the update). background.js relays this to offscreen.js and the
  // backend only while a capture session is actually running for this tab;
  // otherwise it's a harmless no-op message with nothing listening.
  let lastMetadataVideoId = null;

  function maybeReportMetadataChange() {
    const metadata = scrapeVideoMetadata();
    // Don't clobber a good known state with nulls — a transient navigation
    // to a non-video page (channel page, homepage) or a same-page mutation
    // that doesn't actually change video shouldn't overwrite what capture
    // is actually hearing right now.
    if (!metadata.channelName) return;
    if (metadata.videoId && metadata.videoId === lastMetadataVideoId) return;
    if (metadata.videoId) lastMetadataVideoId = metadata.videoId;
    chrome.runtime.sendMessage({ type: "VIDEO_METADATA_UPDATED", metadata }).catch(() => {});
  }

  document.addEventListener("yt-navigate-finish", maybeReportMetadataChange);

  chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
    if (message?.type === "SHOW_OVERLAY" && message.tabId != null) {
      buildPanel(message.tabId);
    } else if (message?.type === "SCRAPE_METADATA") {
      const metadata = scrapeVideoMetadata();
      if (metadata.videoId) lastMetadataVideoId = metadata.videoId;
      sendResponse(metadata);
    }
  });
})();
