// Tapping "Add to my calendar" downloads the event; show it as added straight away
// (the server records the same thing when the file is downloaded).
(function () {
  "use strict";
  document.querySelectorAll("a.cal-button[href$='calendar.ics']").forEach(function (a) {
    a.addEventListener("click", function () {
      setTimeout(function () {
        var done = document.createElement("p");
        done.className = "meta on-cal";
        done.textContent = "\u2713 On your calendar";
        var already = a.nextElementSibling;
        if (already && already.classList.contains("already-form")) already.remove();
        a.replaceWith(done);
      }, 300);
    });
  });
})();
