(() => {
  try {
    const saved = localStorage.getItem('arena-theme');
    if (saved === 'light' || saved === 'dark') document.documentElement.dataset.theme = saved;
  } catch {}
})();
