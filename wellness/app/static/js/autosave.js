// Autosave for journal forms: posts the form a couple of seconds after typing stops.
// Text is only ever sent to the server (encrypted there); nothing is kept in the browser.
(function () {
  "use strict";
  var form = document.querySelector("form[data-autosave]");
  if (!form || !window.fetch) return;
  var status = document.querySelector("[data-autosave-status]");
  var timer = null, inFlight = false, pending = false, dirty = false;

  function say(text) { if (status) status.textContent = text; }

  function save() {
    if (inFlight) { pending = true; return; }
    inFlight = true; dirty = false;
    say("Saving…");
    fetch(form.action || window.location.href, {
      method: "POST",
      body: new FormData(form),
      headers: { "X-Autosave": "1" },
      credentials: "same-origin"
    }).then(function (r) {
      say(r.status === 204 ? "Draft saved · private to you"
        : r.status === 413 ? "That's longer than we can save. Please trim it a little."
        : "Couldn't save just now. Your text is still here; tap Save reflection.");
    }).catch(function () {
      say("You're offline. Your text is still here; tap Save reflection when you're back.");
    }).then(function () {
      inFlight = false;
      if (pending) { pending = false; save(); }
    });
  }

  form.addEventListener("input", function () {
    dirty = true;
    clearTimeout(timer);
    timer = setTimeout(save, 2000);
  });
  window.addEventListener("pagehide", function () {
    if (dirty && navigator.sendBeacon) {
      // sendBeacon can't set headers, so this is a normal save (redirect ignored).
      navigator.sendBeacon(form.action || window.location.href, new FormData(form));
    }
  });
})();
