// After "Save & add to my calendar": open the calendar file straight away, so saving
// and adding happen in one tap. The phone shows its own "Add to Calendar" sheet.
(function () {
  "use strict";
  var el = document.querySelector("[data-open-calendar]");
  if (!el) return;
  var url = el.getAttribute("data-open-calendar");
  if (window.history && window.history.replaceState) {
    // Drop ?added= so a refresh doesn't reopen it.
    window.history.replaceState(null, "", window.location.pathname + "#intention");
  }
  window.location.href = url;
})();

// Intentions form: tap-to-fill starter ideas and a live preview of the sentence.
(function () {
  "use strict";
  var form = document.querySelector("form[data-intention]");
  if (!form) return;
  var out = form.querySelector("[data-sentence]");
  function val(name) { var el = form.elements[name]; return el ? el.value.trim() : ""; }
  function kind() { var k = form.querySelector("input[name=kind]:checked"); return k ? k.value : "when"; }
  function render() {
    var text = "";
    if (kind() === "after") {
      if (val("cue") && val("behavior")) text = "“After I " + val("cue") + ", I will " + val("behavior") + ".”";
    } else if (val("behavior") && (val("time") || val("place"))) {
      text = "“I will " + [val("behavior"), val("time"), val("place")].filter(Boolean).join(" ").replace(/\.$/, "") + ".”";
    }
    out.textContent = text;
    out.hidden = !text;
  }
  form.querySelectorAll(".starter").forEach(function (b) {
    b.addEventListener("click", function () {
      form.elements.behavior.value = b.getAttribute("data-habit");
      form.querySelectorAll(".starter").forEach(function (x) { x.classList.toggle("on", x === b); });
      render();
      form.elements.behavior.focus();
    });
  });
  form.addEventListener("input", render);
  form.addEventListener("change", render);
  render();
})();
