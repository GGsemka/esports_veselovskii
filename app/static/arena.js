(() => {
  const root = document.documentElement;
  const themeToggle = document.querySelector('[data-theme-toggle]');
  const refreshThemeState = () => {
    const light = root.dataset.theme === 'light';
    themeToggle?.setAttribute('aria-pressed', String(light));
  };

  refreshThemeState();
  themeToggle?.addEventListener('click', () => {
    root.dataset.theme = root.dataset.theme === 'light' ? 'dark' : 'light';
    refreshThemeState();
    try {
      localStorage.setItem('arena-theme', root.dataset.theme);
    } catch {}
  });

  const header = document.querySelector('.site-header');
  const sections = header.querySelector('.sections-menu');
  const sectionsButton = sections?.querySelector('summary');
  const sectionsPanel = sections?.querySelector('.sections-panel');

  const placeSections = () => {
    if (!sections?.open) return;
    sectionsPanel.style.removeProperty('--sections-shift');
    const bounds = sectionsPanel.getBoundingClientRect();
    const margin = 12;
    const right = root.clientWidth - margin;
    const shift = Math.max(margin - bounds.left, 0) - Math.max(bounds.right - right, 0);
    sectionsPanel.style.setProperty('--sections-shift', `${shift}px`);
  };

  const syncHeaderHeight = () => {
    root.style.setProperty('--site-header-height', `${header.offsetHeight}px`);
    placeSections();
  };

  syncHeaderHeight();
  new ResizeObserver(syncHeaderHeight).observe(header);
  window.addEventListener('resize', placeSections);
  if (sections) {
    sections.addEventListener('toggle', placeSections);
    document.addEventListener('pointerdown', event => {
      if (sections.open && !sections.contains(event.target)) sections.open = false;
    });
    document.addEventListener('keydown', event => {
      if (event.key !== 'Escape' || !sections.open) return;
      event.preventDefault();
      sections.open = false;
      sectionsButton.focus({ preventScroll: true });
    });
  }

  const filters = document.querySelector('[data-catalog-filters]');
  if (filters) {
    let searchDelay;
    let pending;
    let displayedQuery = window.location.search;
    const results = document.querySelector('#catalog-results');
    const count = document.querySelector('.catalog-count');

    const refreshCatalog = async (url, fromHistory = false) => {
      const request = new AbortController();
      pending = request;
      try {
        const response = await fetch(url, { signal: request.signal });
        if (!response.ok) throw new Error('Catalog request failed');
        const documentUpdate = new DOMParser().parseFromString(await response.text(), 'text/html');
        const nextResults = documentUpdate.querySelector('#catalog-results');
        if (!nextResults) throw new Error('Catalog results are missing');
        if (request.signal.aborted) return;

        results.replaceChildren(...nextResults.childNodes);
        if (count) count.textContent = documentUpdate.querySelector('.catalog-count')?.textContent ?? '';
        if (fromHistory) {
          for (const field of documentUpdate.querySelector('[data-catalog-filters]').elements) {
            if (field.name) filters.elements.namedItem(field.name).value = field.value;
          }
        } else if (url.href !== window.location.href) {
          history.pushState(null, '', url);
        }
        displayedQuery = url.search;
      } catch {
        if (request.signal.aborted) return;
        if (fromHistory) window.location.reload();
        else window.location.assign(url);
      }
    };

    const queueFilters = delay => {
      clearTimeout(searchDelay);
      pending?.abort();
      searchDelay = setTimeout(() => {
        const url = new URL(filters.action);
        url.search = new URLSearchParams(new FormData(filters));
        url.hash = window.location.hash;
        refreshCatalog(url);
      }, delay);
    };

    filters.addEventListener('submit', event => {
      event.preventDefault();
      queueFilters(0);
    });
    filters.addEventListener('change', event => {
      if (event.target.matches('select')) queueFilters(0);
    });
    filters.addEventListener('input', event => {
      if (event.target.matches('input[type="search"]') && !event.isComposing) queueFilters(250);
    });
    window.addEventListener('popstate', () => {
      if (displayedQuery === window.location.search) return;
      clearTimeout(searchDelay);
      pending?.abort();
      refreshCatalog(new URL(window.location.href), true);
    });
  }

  const motion = window.matchMedia('(prefers-reduced-motion: reduce)');
  for (const poster of document.querySelectorAll('[data-preview-video]')) {
    const card = poster.closest('.game-feature');
    let preview;

    const stopPreview = () => {
      if (!preview) return;
      const video = preview;
      preview = undefined;
      video.pause();
      video.removeAttribute('src');
      video.replaceChildren();
      video.load();
      video.remove();
    };

    card.addEventListener('pointerenter', event => {
      if (event.pointerType !== 'mouse' || motion.matches || preview) return;
      const video = document.createElement('video');
      video.className = 'game-poster-preview';
      video.hidden = true;
      video.muted = true;
      video.defaultMuted = true;
      video.loop = true;
      video.playsInline = true;
      video.setAttribute('aria-hidden', 'true');
      video.addEventListener('playing', () => { video.hidden = false; }, { once: true });
      const showPoster = () => {
        if (preview === video) stopPreview();
      };
      video.addEventListener('error', showPoster, { once: true });
      preview = video;
      if (poster.dataset.previewFallback) {
        const original = document.createElement('source');
        original.src = poster.dataset.previewVideo;
        original.type = 'video/mp4; codecs="hvc1"';
        const fallback = document.createElement('source');
        fallback.src = poster.dataset.previewFallback;
        fallback.type = 'video/mp4';
        fallback.addEventListener('error', showPoster, { once: true });
        video.append(original, fallback);
      } else {
        video.src = poster.dataset.previewVideo;
      }
      poster.append(video);
      video.play().catch(showPoster);
    });

    card.addEventListener('pointerleave', stopPreview);
    motion.addEventListener('change', stopPreview);
  }

  for (const form of document.querySelectorAll('[data-confirm]')) {
    form.addEventListener('submit', event => {
      if (!window.confirm(form.dataset.confirm)) event.preventDefault();
    });
  }

  const validate = (form, target, error) => {
    const update = () => target.setCustomValidity(error());
    form.addEventListener('input', update);
    form.addEventListener('change', update);
    update();
  };

  for (const form of document.querySelectorAll('[data-password-match]')) {
    const password = form.elements.namedItem('password');
    const confirm = form.elements.namedItem('confirm');
    validate(form, confirm, () => password.value === confirm.value ? '' : 'Пароли должны совпадать.');
  }

  for (const form of document.querySelectorAll('[data-date-range]')) {
    const start = form.elements.namedItem('start_date');
    const end = form.elements.namedItem('end_date');
    validate(form, end, () => start.value && end.value && start.value >= end.value
      ? 'Окончание должно быть позже начала.' : '');
  }

  for (const form of document.querySelectorAll('[data-game-format]')) {
    const game = form.elements.namedItem('game_id');
    const registrationType = form.elements.namedItem('registration_type');
    const selectedFormat = () => game.selectedOptions[0]?.dataset.format;
    game.addEventListener('change', () => {
      if (selectedFormat()) registrationType.value = selectedFormat();
    });
    validate(form, registrationType, () => selectedFormat() && registrationType.value !== selectedFormat()
      ? 'Вид участия должен соответствовать дисциплине.' : '');
  }

  for (const form of document.querySelectorAll('[data-no-tie]')) {
    const a = form.elements.namedItem('score1');
    const b = form.elements.namedItem('score2');
    validate(form, b, () => a.value && b.value && Number(a.value) === Number(b.value)
      ? 'В матче на выбывание укажите победившую сторону.' : '');
  }

  for (const form of document.querySelectorAll('[data-team-sizes]')) {
    const format = form.elements.namedItem('format');
    const min = form.elements.namedItem('team_size_min');
    const max = form.elements.namedItem('team_size_max');
    validate(form, max, () => {
      if (!min.value || !max.value) return '';
      if (format.value === 'solo' && (Number(min.value) !== 1 || Number(max.value) !== 1)) {
        return 'Для одиночного формата оба значения должны быть равны 1.';
      }
      return Number(min.value) > Number(max.value) ? 'Максимум должен быть не меньше минимума.' : '';
    });
  }

  for (const input of document.querySelectorAll('[data-image-file]')) {
    input.addEventListener('change', () => {
      const file = input.files[0];
      let error = '';
      if (file && !/\.(jpe?g|png|webp)$/i.test(file.name)) error = 'Выберите JPG, PNG или WebP.';
      if (file && file.size > 1024 * 1024) error = 'Размер файла не должен превышать 1 МБ.';
      input.setCustomValidity(error);
    });
  }

  const tree = document.querySelector('[data-bracket-tree]');
  if (tree && typeof ResizeObserver !== 'undefined') {
    const wires = tree.querySelector('.bracket-wires');
    const branches = [...tree.querySelectorAll('[data-next-match]')];
    const redraw = () => {
      const origin = tree.getBoundingClientRect();
      const paths = document.createDocumentFragment();
      for (const branch of branches) {
        const next = document.getElementById('match-' + branch.dataset.nextMatch);
        if (!next) continue;
        const from = branch.getBoundingClientRect();
        const to = next.getBoundingClientRect();
        const x1 = from.right - origin.left;
        const x2 = to.left - origin.left;
        const y1 = from.top + from.height / 2 - origin.top;
        const y2 = to.top + to.height / 2 - origin.top;
        const midpoint = (x1 + x2) / 2;
        const wire = document.createElementNS('http://www.w3.org/2000/svg', 'path');
        wire.setAttribute('d', `M ${x1} ${y1} H ${midpoint} V ${y2} H ${x2}`);
        paths.append(wire);
      }
      wires.replaceChildren(paths);
    };
    const observer = new ResizeObserver(redraw);
    observer.observe(tree);
    for (const match of tree.querySelectorAll('.match-tile')) observer.observe(match);
  }

  const feed = document.querySelector('[data-chat-feed]');
  if (!feed) return;

  const organizerId = Number(feed.dataset.organizerUser);
  let after = Number(feed.dataset.chatAfter);
  let pending = false;
  feed.scrollTop = feed.scrollHeight;

  const messageElement = message => {
    const item = document.createElement('article');
    item.className = `chat-message ${Number(message.sender_id) === organizerId ? 'chat--left' : 'chat--right'}`;

    const byline = document.createElement('div');
    byline.className = 'chat-byline';
    const author = document.createElement('strong');
    author.textContent = message.nickname;
    const date = document.createElement('time');
    date.textContent = message.created_at;
    byline.append(author, date);

    const content = document.createElement('p');
    content.textContent = message.text;
    item.append(byline, content);
    return item;
  };

  const poll = async () => {
    if (pending || document.hidden) return;
    pending = true;
    try {
      const url = new URL(feed.dataset.chatFeed, window.location.href);
      url.searchParams.set('after', String(after));
      const response = await fetch(url, { headers: { Accept: 'application/json' }, cache: 'no-store' });
      if (!response.ok || response.redirected) return;
      const data = await response.json();
      if (!Array.isArray(data.messages) || !data.messages.length) return;

      const atBottom = feed.scrollHeight - feed.scrollTop - feed.clientHeight < 60;
      const fragment = document.createDocumentFragment();
      for (const message of data.messages) {
        const id = Number(message.id);
        if (id <= after) continue;
        fragment.append(messageElement(message));
        after = id;
      }
      feed.querySelector('.chat-empty')?.remove();
      feed.append(fragment);
      if (atBottom) feed.scrollTop = feed.scrollHeight;
    } catch {
      // A later poll can resume from the last message rendered.
    } finally {
      pending = false;
    }
  };

  window.setInterval(poll, 8000);
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden) poll();
  });
})();
