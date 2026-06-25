/* Screen-only viewer: arrow-key navigation + page counter. Disabled under print. */
(function () {
  var root = document.documentElement;
  var KEY = 'gaia-deck-theme';
  try { root.setAttribute('data-theme', localStorage.getItem(KEY) || 'light'); } catch (e) {}
  var toggle = document.querySelector('.theme-toggle');
  if (toggle) toggle.addEventListener('click', function () {
    var t = root.getAttribute('data-theme') === 'dark' ? 'light' : 'dark';
    root.setAttribute('data-theme', t);
    try { localStorage.setItem(KEY, t); } catch (e) {}
  });
  var slides = Array.prototype.slice.call(document.querySelectorAll('.deck > .slide'));
  slides.forEach(function (s, i) {
    var p = document.createElement('div');
    p.className = 'pageno';
    p.textContent = String(i + 1).padStart(2, '0') + ' / ' + String(slides.length).padStart(2, '0');
    s.appendChild(p);
  });
  var cur = 0;
  function go(i) {
    cur = Math.max(0, Math.min(slides.length - 1, i));
    slides[cur].scrollIntoView({ behavior: 'smooth', block: 'start' });
  }
  document.addEventListener('keydown', function (e) {
    if (e.key === 'ArrowRight' || e.key === 'PageDown') { go(cur + 1); e.preventDefault(); }
    else if (e.key === 'ArrowLeft' || e.key === 'PageUp') { go(cur - 1); e.preventDefault(); }
  });
})();
