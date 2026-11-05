// Session replays are watch-only: no "Save video as…" menu on the player.
(function () {
  "use strict";
  document.querySelectorAll("video[data-replay]").forEach(function (v) {
    v.addEventListener("contextmenu", function (e) { e.preventDefault(); });
  });
})();
