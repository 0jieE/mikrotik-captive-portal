/* Adds a show/hide (eye) button to every password box on the page. No dependencies. */
(function () {
  function enhance(input) {
    if (input.dataset.pwToggle) return;
    input.dataset.pwToggle = "1";

    var wrap = document.createElement("div");
    wrap.className = "pw-wrap";
    input.parentNode.insertBefore(wrap, input);
    wrap.appendChild(input);

    var btn = document.createElement("button");
    btn.type = "button";  // never submits the form
    btn.className = "pw-toggle";
    btn.setAttribute("aria-label", "Show password");
    btn.setAttribute("aria-pressed", "false");
    btn.title = "Show password";
    btn.innerHTML = '<i class="bi bi-eye" aria-hidden="true"></i>';
    wrap.appendChild(btn);

    btn.addEventListener("click", function () {
      var show = input.type === "password";
      input.type = show ? "text" : "password";
      var label = show ? "Hide password" : "Show password";
      btn.setAttribute("aria-label", label);
      btn.setAttribute("aria-pressed", show ? "true" : "false");
      btn.title = label;
      btn.innerHTML = '<i class="bi ' + (show ? "bi-eye-slash" : "bi-eye") + '" aria-hidden="true"></i>';
      input.focus();
    });

    // hide the password again when the form is sent, so the browser does not save it as plain text in history
    if (input.form) {
      input.form.addEventListener("submit", function () { input.type = "password"; });
    }
  }

  function run() {
    document.querySelectorAll('input[type="password"]').forEach(enhance);
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", run);
  else run();
})();
