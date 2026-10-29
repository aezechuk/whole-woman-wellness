// Pages behind the journal PIN: hide the content when the app goes to the background
// (so the app switcher shows a blank card), and lock after 15 minutes away or idle.
(function () {
  "use strict";
  var body = document.body;
  var form = document.querySelector("[data-lock-form]");
  if (!form) return;
  var limit = (parseInt(body.getAttribute("data-lock-after"), 10) || 900) * 1000;
  var last = Date.now();

  function lockIdle() {
    form.querySelector("[name=idle]").value = "1";
    form.submit();
  }
  ["input", "keydown", "pointerdown", "scroll", "touchstart"].forEach(function (ev) {
    window.addEventListener(ev, function () { last = Date.now(); }, { passive: true });
  });
  document.addEventListener("visibilitychange", function () {
    if (document.hidden) {
      body.classList.add("veiled");
    } else if (Date.now() - last >= limit) {
      lockIdle();
    } else {
      body.classList.remove("veiled");
    }
  });
  window.addEventListener("pageshow", function (e) {
    // Coming back via the Back button from the browser cache: re-check with the server.
    if (e.persisted) window.location.reload();
  });
  setInterval(function () { if (Date.now() - last >= limit) lockIdle(); }, 30000);
})();
