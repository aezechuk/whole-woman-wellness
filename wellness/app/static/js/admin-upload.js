// Admin upload: ask the app for a signed upload link, send the file straight to the
// private S3 bucket (with progress), then tell the app it arrived.
(function () {
  "use strict";
  var form = document.querySelector("[data-upload-form]");
  if (!form) return;
  var token = document.querySelector("meta[name=csrf-token]").content;
  var bar = form.querySelector("[data-upload-progress]");
  var status = form.querySelector("[data-upload-status]");
  var button = form.querySelector("button[type=submit]");

  function say(t) { status.textContent = t; }
  function post(url, data) {
    data.append("csrf_token", token);
    return fetch(url, { method: "POST", body: data, credentials: "same-origin", headers: { "X-CSRFToken": token } })
      .then(function (r) { return r.json().then(function (j) { if (!r.ok) throw new Error(j.error || "Something went wrong."); return j; }); });
  }

  form.addEventListener("submit", function (e) {
    e.preventDefault();
    var file = form.querySelector("[name=file]").files[0];
    if (!file) return;
    var d = new FormData();
    ["week_key", "kind", "title"].forEach(function (n) { d.append(n, form.querySelector("[name=" + n + "]").value); });
    d.append("filename", file.name);
    d.append("size", String(file.size));
    button.disabled = true;
    say("Getting ready…");
    post("/admin/resources/upload-url", d).then(function (j) {
      return new Promise(function (resolve, reject) {
        var x = new XMLHttpRequest();
        x.open("PUT", j.url);
        x.setRequestHeader("Content-Type", j.content_type);
        bar.hidden = false;
        x.upload.onprogress = function (ev) {
          if (ev.lengthComputable) { bar.value = Math.round(100 * ev.loaded / ev.total); say("Uploading… " + bar.value + "%"); }
        };
        x.onload = function () { x.status < 300 ? resolve(j.id) : reject(new Error("Upload failed (" + x.status + "). Please try again.")); };
        x.onerror = function () { reject(new Error("Upload was interrupted. Check your connection and try again.")); };
        x.send(file);
      });
    }).then(function (id) {
      say("Finishing…");
      return post("/admin/resources/" + id + "/done", new FormData());
    }).then(function () {
      window.location.reload();
    }).catch(function (err) {
      say(err.message);
      button.disabled = false;
    });
  });
  window.addEventListener("beforeunload", function (e) {
    if (button.disabled) { e.preventDefault(); e.returnValue = ""; }
  });
})();
