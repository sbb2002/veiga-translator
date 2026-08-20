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
    Object.assign(root.style, {
      position: "fixed",
      top: "72px",
      right: "16px",
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
    const closeBtn = document.createElement("button");
    closeBtn.textContent = "✕";
    closeBtn.title = "패널 닫기 (캡처는 계속됨)";
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
    closeBtn.addEventListener("click", removePanel);
    header.appendChild(title);
    header.appendChild(closeBtn);

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

    const resizeGrip = document.createElement("div");
    Object.assign(resizeGrip.style, {
      position: "absolute",
      right: "0",
      bottom: "0",
      width: "16px",
      height: "16px",
      cursor: "nwse-resize",
      zIndex: "1",
    });

    root.style.position = "fixed"; // (kept explicit; resize handle needs root to stay the positioned ancestor)
    root.appendChild(header);
    root.appendChild(iframe);
    root.appendChild(resizeGrip);
    document.documentElement.appendChild(root);

    rootEl = root;
    iframeEl = iframe;

    makeDraggable(header, root, iframe);
    makeResizable(resizeGrip, root, iframe);
    followFullscreen(root);
  }

  function removePanel() {
    if (!rootEl) return;
    rootEl.remove();
    rootEl = null;
    iframeEl = null;
  }

  // Dragging moves the root by absolute left/top — switch off the initial
  // right-anchored position on first drag so the panel doesn't jump.
  function makeDraggable(handle, root, iframe) {
    handle.addEventListener("mousedown", (downEvent) => {
      downEvent.preventDefault();
      const rect = root.getBoundingClientRect();
      root.style.right = "";
      root.style.left = `${rect.left}px`;
      root.style.top = `${rect.top}px`;

      const startX = downEvent.clientX;
      const startY = downEvent.clientY;
      const startLeft = rect.left;
      const startTop = rect.top;
      iframe.style.pointerEvents = "none"; // let mousemove keep tracking over the iframe

      function onMove(moveEvent) {
        const dx = moveEvent.clientX - startX;
        const dy = moveEvent.clientY - startY;
        root.style.left = `${Math.max(0, startLeft + dx)}px`;
        root.style.top = `${Math.max(0, startTop + dy)}px`;
      }
      function onUp() {
        iframe.style.pointerEvents = "";
        document.removeEventListener("mousemove", onMove);
        document.removeEventListener("mouseup", onUp);
      }
      document.addEventListener("mousemove", onMove);
      document.addEventListener("mouseup", onUp);
    });
  }

  function makeResizable(grip, root, iframe) {
    grip.addEventListener("mousedown", (downEvent) => {
      downEvent.preventDefault();
      downEvent.stopPropagation();
      const rect = root.getBoundingClientRect();
      const startX = downEvent.clientX;
      const startY = downEvent.clientY;
      const startWidth = rect.width;
      const startHeight = rect.height;
      iframe.style.pointerEvents = "none";

      function onMove(moveEvent) {
        const dx = moveEvent.clientX - startX;
        const dy = moveEvent.clientY - startY;
        root.style.width = `${Math.max(MIN_WIDTH, startWidth + dx)}px`;
        root.style.height = `${Math.max(MIN_HEIGHT, startHeight + dy)}px`;
      }
      function onUp() {
        iframe.style.pointerEvents = "";
        document.removeEventListener("mousemove", onMove);
        document.removeEventListener("mouseup", onUp);
      }
      document.addEventListener("mousemove", onMove);
      document.addEventListener("mouseup", onUp);
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

  chrome.runtime.onMessage.addListener((message) => {
    if (message?.type === "SHOW_OVERLAY" && message.tabId != null) {
      buildPanel(message.tabId);
    }
  });
})();
