/**
 * HDR to SDR - site interactions
 *
 * Responsibilities:
 *   1. Wire up the drag/touch comparison slider.
 *   2. Animate the slider into view on first scroll intersection.
 */

(function () {
  "use strict";

  // ── Comparison slider ──────────────────────────────────────────────────────

  const container = document.getElementById("comparisonContainer");
  const imgBefore = document.getElementById("img-before");
  const imgAfter  = document.getElementById("img-after");
  const handle    = document.getElementById("sliderHandle");

  if (!container || !imgBefore || !imgAfter || !handle) return;

  let sliderPos  = 0.5;
  let isDragging = false;

  function applySlider() {
    const pct = (sliderPos * 100).toFixed(3);
    imgBefore.style.clipPath = `inset(0 ${(100 - sliderPos * 100).toFixed(3)}% 0 0)`;
    handle.style.left = pct + "%";
  }

  function clamp01(v) {
    return Math.max(0, Math.min(1, v));
  }

  function posFromEvent(e) {
    const rect    = container.getBoundingClientRect();
    const clientX = e.touches ? e.touches[0].clientX : e.clientX;
    return clamp01((clientX - rect.left) / rect.width);
  }

  container.addEventListener("mousedown", (e) => {
    isDragging = true;
    sliderPos  = posFromEvent(e);
    applySlider();
    e.preventDefault();
  });

  document.addEventListener("mousemove", (e) => {
    if (!isDragging) return;
    sliderPos = posFromEvent(e);
    applySlider();
  });

  document.addEventListener("mouseup", () => { isDragging = false; });

  container.addEventListener("touchstart", (e) => {
    isDragging = true;
    sliderPos  = posFromEvent(e);
    applySlider();
    e.preventDefault();
  }, { passive: false });

  document.addEventListener("touchmove", (e) => {
    if (!isDragging) return;
    sliderPos = posFromEvent(e);
    applySlider();
    e.preventDefault();
  }, { passive: false });

  document.addEventListener("touchend", () => { isDragging = false; });

  // ── Intro sweep animation ──────────────────────────────────────────────────

  let introPlayed = false;

  function playIntro() {
    if (introPlayed) return;
    introPlayed = true;

    // Sweep from right (1.0) to centre (0.5)
    sliderPos = 1;
    applySlider();

    const target = 0.5;
    function step() {
      sliderPos += (target - sliderPos) * 0.07;
      applySlider();
      if (Math.abs(sliderPos - target) > 0.001) requestAnimationFrame(step);
      else { sliderPos = target; applySlider(); }
    }
    setTimeout(() => requestAnimationFrame(step), 300);
  }

  const io = new IntersectionObserver(
    (entries) => { if (entries[0].isIntersecting) { playIntro(); io.disconnect(); } },
    { threshold: 0.35 }
  );
  io.observe(container);

  applySlider();
})();
