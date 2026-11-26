/* The verification portal's only script (served as a file: the CSP allows no inline script). Everything works without it; it adds:
   the power button (replays the switch-on), "Save as PDF" (the browser's print dialog), and a forgiving code box (any case, spaces, no dashes,
   a pasted link, the letter O for the zero of L3M0N) that goes straight to the certificate's page.
   normalize() must agree with tools/l3mon/verify/codes.py normalize(): tools/tests/test_verify_site.py runs both on the same inputs. */
(function () {
  "use strict";
  var PREFIX = "L3M0N26", CATS = ["ORG", "PAR", "ACH", "APP"], ALPHABET = "BCDFGHJKLMNPQRSTVWXYZ";
  var CANON = new RegExp("^" + PREFIX + "-(" + CATS.join("|") + ")-([" + ALPHABET + "]{5})$");

  function normalize(text) {
    if (typeof text !== "string" || text.length > 200) return null;
    var s = text.trim();
    if (s.indexOf("/") !== -1) { s = s.replace(/\/+$/, ""); s = s.slice(s.lastIndexOf("/") + 1); }
    s = s.toUpperCase().replace(/[\s\-_.]+/g, "");
    var m = new RegExp("^L3M[0O]N26(" + CATS.join("|") + ")([A-Z]{5})$").exec(s);
    if (!m) return null;
    var code = PREFIX + "-" + m[1] + "-" + m[2];
    return CANON.test(code) ? code : null;
  }
  window.l3monNormalize = normalize;

  document.documentElement.classList.add("js");

  function replay() {
    var b = document.body;
    b.classList.remove("play");
    void b.offsetWidth;
    b.classList.add("play");
  }

  function wire() {
    var power = document.getElementById("power");
    if (power) power.addEventListener("click", replay);
    var print = document.getElementById("print");
    if (print) print.addEventListener("click", function () { window.print(); });

    var form = document.getElementById("lookup");
    if (form) {
      var input = form.querySelector("input[name=code]"), err = document.getElementById("lookup-err");
      form.addEventListener("submit", function (ev) {
        ev.preventDefault();
        var code = normalize(input.value);
        if (code) { window.location.assign("/" + code); return; }
        err.textContent = input.value.trim()
          ? "That does not look like a certificate code. It reads like L3M0N26-PAR-BKXQT: a category and five letters."
          : "Type the code printed on the certificate.";
        input.focus();
      });
    }

    // an address in another form (lower case, no dashes, a trailing slash...) reached "No signal": go to the canonical address once
    if (document.body.classList.contains("st-missing")) {
      var path = window.location.pathname, code = null;
      try { code = normalize(decodeURIComponent(path.replace(/\/+$/, "").split("/").pop() || "")); } catch (e) { code = null; }
      if (code && "/" + code !== path) window.location.replace("/" + code);
    }
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", wire);
  else wire();
})();
