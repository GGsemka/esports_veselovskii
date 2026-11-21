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
