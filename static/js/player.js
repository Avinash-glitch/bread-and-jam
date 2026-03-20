(function () {
  'use strict';
  let currentAudio = null;
  let currentBtn = null;

  document.addEventListener('click', function (e) {
    const btn = e.target.closest('[data-play-btn]');
    if (!btn) return;
    const card = btn.closest('[data-track-card]');
    if (!card) return;
    const audio = card.querySelector('audio');
    if (!audio) return;

    if (currentAudio && currentAudio !== audio) {
      currentAudio.pause();
      currentAudio.currentTime = 0;
      if (currentBtn) currentBtn.textContent = '\u25B6';
    }

    if (audio.paused) {
      audio.play();
      btn.textContent = '\u23F8';
      currentAudio = audio;
      currentBtn = btn;
      const projectId = card.dataset.projectId;
      if (projectId) {
        fetch('/projects/' + projectId + '/listen', {
          method: 'POST',
          headers: { 'X-Requested-With': 'XMLHttpRequest' }
        });
      }
    } else {
      audio.pause();
      btn.textContent = '\u25B6';
    }
  });
})();
