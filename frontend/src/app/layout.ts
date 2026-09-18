const STORAGE_KEY = 'fithealth-layout-v2';
const MIN_CHAT_WIDTH = 480;
const MIN_SIDEBAR_WIDTH = 380;
const DEFAULT_SIDEBAR_WIDTH = 520;
const BREAKPOINT = '(max-width: 1050px)';

export function installLayout(document: Document, window: Window): () => void {
  const grid = document.querySelector<HTMLElement>('.main-grid');
  const sidebar = document.querySelector<HTMLElement>('.sidebar');
  const resizer = document.querySelector<HTMLElement>('#layout-resizer');
  if (!grid || !sidebar || !resizer) return () => undefined;
  let dragging = false;
  const apply = (requested: number) => {
    if (window.matchMedia(BREAKPOINT).matches) {
      grid.style.removeProperty('--workout-width');
      return;
    }
    const available = grid.clientWidth - 16;
    if (available <= MIN_CHAT_WIDTH + MIN_SIDEBAR_WIDTH) return;
    const width = Math.max(
      MIN_SIDEBAR_WIDTH,
      Math.min(requested || DEFAULT_SIDEBAR_WIDTH, available - MIN_CHAT_WIDTH),
    );
    grid.style.setProperty('--chat-width', `${available - width}px`);
    grid.style.setProperty('--workout-width', `${Math.round(width)}px`);
    resizer.setAttribute('aria-valuenow', String(Math.round(width)));
  };
  const saved = () => {
    try {
      const parsed = JSON.parse(window.localStorage.getItem(STORAGE_KEY) || '{}');
      return Number(parsed.workoutWidth) || DEFAULT_SIDEBAR_WIDTH;
    } catch {
      return DEFAULT_SIDEBAR_WIDTH;
    }
  };
  const persist = () =>
    window.localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({ workoutWidth: Math.round(sidebar.getBoundingClientRect().width) }),
    );
  const down = (event: PointerEvent) => {
    if (window.matchMedia(BREAKPOINT).matches) return;
    dragging = true;
    resizer.classList.add('dragging');
    resizer.setPointerCapture(event.pointerId);
    event.preventDefault();
  };
  const move = (event: PointerEvent) => {
    if (dragging) apply(grid.getBoundingClientRect().right - event.clientX);
  };
  const finish = (event: PointerEvent) => {
    if (!dragging) return;
    dragging = false;
    resizer.classList.remove('dragging');
    if (resizer.hasPointerCapture(event.pointerId)) resizer.releasePointerCapture(event.pointerId);
    persist();
  };
  const keydown = (event: KeyboardEvent) => {
    if (!['ArrowLeft', 'ArrowRight'].includes(event.key) || window.matchMedia(BREAKPOINT).matches)
      return;
    event.preventDefault();
    apply(sidebar.getBoundingClientRect().width + (event.key === 'ArrowLeft' ? 24 : -24));
    persist();
  };
  const resize = () => apply(saved());
  resizer.addEventListener('pointerdown', down);
  resizer.addEventListener('pointermove', move);
  resizer.addEventListener('pointerup', finish);
  resizer.addEventListener('pointercancel', finish);
  resizer.addEventListener('keydown', keydown);
  window.addEventListener('resize', resize);
  window.requestAnimationFrame(resize);
  return () => {
    resizer.removeEventListener('pointerdown', down);
    resizer.removeEventListener('pointermove', move);
    resizer.removeEventListener('pointerup', finish);
    resizer.removeEventListener('pointercancel', finish);
    resizer.removeEventListener('keydown', keydown);
    window.removeEventListener('resize', resize);
  };
}
