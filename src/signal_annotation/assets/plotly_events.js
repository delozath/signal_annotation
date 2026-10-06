/* Minimal Dash bridge: event observation only, never geometry or persistence. */
(function () {
  "use strict";

  var graphId = "signal-graph";
  var storeId = "bridge-event-store";
  var installed = false;
  var nonce = 0;

  function isEditableTarget(target) {
    return !!(target && target.closest && target.closest(
      "input, textarea, select, button, [contenteditable]"
    ));
  }

  function storeHasSelection() {
    var app = document.getElementById("signal-annotation-app");
    return !!(app && app.dataset.hasSelection === "true");
  }

  function publish(event) {
    if (window.dash_clientside && window.dash_clientside.set_props) {
      nonce += 1;
      event.nonce = nonce;
      window.dash_clientside.set_props(storeId, {data: event});
    }
  }

  function attach() {
    var graph = document.getElementById(graphId);
    var plot = graph && graph.querySelector(".js-plotly-plot");
    if (!plot || plot.__signalAnnotationBridge) return;
    plot.__signalAnnotationBridge = true;
    plot.on("plotly_click", function (event) {
      var point = event && event.points && event.points[0];
      publish({
        type: "graph-click",
        ctrlKey: !!(event && event.event && event.event.ctrlKey),
        metaKey: !!(event && event.event && event.event.metaKey),
        identity: point ? point.customdata : null
      });
    });
  }

  document.addEventListener("keydown", function (event) {
    if ((event.key === "Delete" || event.key === "Backspace") &&
        !isEditableTarget(event.target) && storeHasSelection()) {
      publish({type: "delete-request", key: event.key});
    }
  }, true);

  function install() {
    if (!installed) {
      installed = true;
      new MutationObserver(attach).observe(document.documentElement, {childList: true, subtree: true});
    }
    attach();
  }

  window.addEventListener("DOMContentLoaded", install);
  install();
}());
