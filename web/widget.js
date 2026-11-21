/*
 * Clinic assistant: one-line embed for any website.
 *
 *   <script src="https://YOUR-SERVER/widget.js" defer></script>
 *
 * Adds a "Book an appointment" button in the corner of the page. Clicking it opens
 * the assistant in a panel (an iframe of this server's page), where the visitor
 * picks Talk or Type. Closing the panel ends the conversation.
 *
 * Optional attributes on the script tag:
 *   data-label="Book an appointment"   button text
 *   data-language="ar"                 language selected when the panel opens
 *   data-position="left"               left or right corner (default right)
 *   data-color="#0f766e"               button colour
 */
(function () {
  "use strict";
  if (window.__clinicAssistantLoaded) return;
  window.__clinicAssistantLoaded = true;

  var script = document.currentScript;
  if (!script || !script.src) return;
  var base = script.src.replace(/\/widget\.js(\?.*)?$/, "");
  var label = script.getAttribute("data-label") || "Book an appointment";
  var language = script.getAttribute("data-language") || "";
  var side = script.getAttribute("data-position") === "left" ? "left" : "right";
  var color = script.getAttribute("data-color") || "#0f766e";

  var css =
    ".cva-button{position:fixed;bottom:20px;" + side + ":20px;z-index:2147483000;" +
    "display:flex;align-items:center;gap:8px;padding:12px 18px;border:0;border-radius:999px;" +
    "background:" + color + ";color:#fff;font:600 15px/1.2 system-ui,-apple-system,'Segoe UI',sans-serif;" +
    "box-shadow:0 6px 20px rgba(0,0,0,.25);cursor:pointer}" +
    ".cva-button:focus-visible{outline:3px solid #fff;outline-offset:2px}" +
    ".cva-panel{position:fixed;bottom:84px;" + side + ":20px;z-index:2147483000;width:400px;" +
    "height:min(640px,calc(100vh - 110px));border-radius:16px;overflow:hidden;background:#fff;" +
    "box-shadow:0 12px 40px rgba(0,0,0,.3);display:flex;flex-direction:column}" +
    ".cva-bar{display:flex;justify-content:flex-end;padding:6px;background:" + color + "}" +
    ".cva-close{border:0;background:transparent;color:#fff;font:600 22px/1 system-ui,sans-serif;" +
    "width:36px;height:32px;cursor:pointer;border-radius:8px}" +
    ".cva-close:focus-visible{outline:2px solid #fff}" +
    ".cva-frame{flex:1;width:100%;border:0}" +
    "@media (max-width:480px){.cva-panel{left:0;right:0;bottom:0;width:100%;height:100%;border-radius:0}}";

  var style = document.createElement("style");
  style.textContent = css;
  document.head.appendChild(style);

  var button = document.createElement("button");
  button.className = "cva-button";
  button.type = "button";
  button.setAttribute("aria-haspopup", "dialog");
  button.setAttribute("aria-expanded", "false");
  button.textContent = label;

  var panel = null;

  function open() {
    if (panel) return;
    panel = document.createElement("div");
    panel.className = "cva-panel";
    panel.setAttribute("role", "dialog");
    panel.setAttribute("aria-label", label);

    var bar = document.createElement("div");
    bar.className = "cva-bar";
    var close = document.createElement("button");
    close.className = "cva-close";
    close.type = "button";
    close.setAttribute("aria-label", "Close");
    close.textContent = "×";
    close.addEventListener("click", shut);
    bar.appendChild(close);

    var frame = document.createElement("iframe");
    frame.className = "cva-frame";
    frame.title = label;
    frame.allow = "microphone; autoplay";
    frame.src = base + "/index.html?embed=1" + (language ? "&lang=" + encodeURIComponent(language) : "");

    panel.appendChild(bar);
    panel.appendChild(frame);
    document.body.appendChild(panel);
    button.setAttribute("aria-expanded", "true");
    close.focus();
  }

  // Removing the iframe ends any conversation running inside it.
  function shut() {
    if (!panel) return;
    panel.remove();
    panel = null;
    button.setAttribute("aria-expanded", "false");
    button.focus();
  }

  button.addEventListener("click", function () { panel ? shut() : open(); });
  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape") shut();
  });

  function mount() { document.body.appendChild(button); }
  if (document.body) mount(); else document.addEventListener("DOMContentLoaded", mount);
})();
