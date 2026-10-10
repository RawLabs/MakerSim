// Window layout is browser UI state, separate from engineering session memory.
const layoutKey = 'makersim.agentWindowLayout';
const margin = 8;

export class FloatingWindow {
  constructor(element, {title, collapse, close, maximize, onMode, onClose}) {
    this.element = element; this.title = title; this.collapse = collapse; this.maximize = maximize;
    this.onMode = onMode; this.onClose = onClose; this.mode = 'card'; this.drag = null; this.suppressClick = false;
    this.resizeGesture = null;
    this.layouts = {};
    try {
      const saved = JSON.parse(localStorage.getItem(layoutKey));
      for (const mode of ['card', 'pill']) {
        const box = saved?.[mode];
        if (box && ['x', 'y', 'width', 'height'].every(key => Number.isFinite(box[key]))) this.layouts[mode] = box;
      }
    } catch { /* Default position works without browser storage. */ }
    const heading = element.querySelector('.agent-heading');
    collapse.onclick = () => this.setMode('pill');
    close.onclick = () => this.close();
    maximize.onclick = () => this.setMode(this.mode === 'fullscreen' ? 'card' : 'fullscreen');
    heading.addEventListener('click', event => {
      if (event.target.closest('[data-ui="collapse"], [data-ui="close"], [data-ui="maximize"]')) return;
      if (this.suppressClick) {
        this.suppressClick = false; event.preventDefault(); event.stopPropagation(); return;
      }
      if (this.mode === 'pill') this.setMode('card');
    });
    heading.addEventListener('pointerdown', event => {
      if (this.mode === 'fullscreen' || event.button !== 0 || !event.isPrimary ||
          event.target.closest('[data-ui="collapse"], [data-ui="close"], [data-ui="maximize"]')) return;
      const box = element.getBoundingClientRect();
      this.drag = {pointer: event.pointerId, startX: event.clientX, startY: event.clientY,
                   x: box.x, y: box.y, width: box.width, height: box.height, moved: false};
      this.suppressClick = false;
      heading.setPointerCapture(event.pointerId);
    });
    heading.addEventListener('pointermove', event => {
      if (!this.drag || event.pointerId !== this.drag.pointer) return;
      const dx = event.clientX - this.drag.startX, dy = event.clientY - this.drag.startY;
      if (!this.drag.moved && Math.hypot(dx, dy) < 5) return;
      this.drag.moved = true;
      element.classList.add('dragging');
      this.place({...this.drag, x: this.drag.x + dx, y: this.drag.y + dy});
    });
    const finishDrag = event => {
      if (!this.drag || event.pointerId !== this.drag.pointer) return;
      this.suppressClick = this.drag.moved;
      this.drag = null; element.classList.remove('dragging');
      if (heading.hasPointerCapture(event.pointerId)) heading.releasePointerCapture(event.pointerId);
      this.remember();
    };
    heading.addEventListener('pointerup', finishDrag);
    heading.addEventListener('pointercancel', event => { finishDrag(event); this.suppressClick = false; });
    heading.addEventListener('lostpointercapture', event => { if (this.drag) finishDrag(event); });
    title.addEventListener('keydown', event => {
      if (this.mode === 'fullscreen' || !['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown'].includes(event.key)) return;
      event.preventDefault();
      const box = element.getBoundingClientRect(), step = event.shiftKey ? 50 : 20;
      this.place({x: box.x + (event.key === 'ArrowRight' ? step : event.key === 'ArrowLeft' ? -step : 0),
                  y: box.y + (event.key === 'ArrowDown' ? step : event.key === 'ArrowUp' ? -step : 0),
                  width: box.width, height: box.height});
      this.remember();
    });
    this.resizeHandles = [];
    for (const direction of ['n', 'e', 's', 'w', 'ne', 'nw', 'se', 'sw']) {
      const handle = document.createElement(direction === 'se' ? 'button' : 'span');
      handle.className = 'agent-resize-handle'; handle.dataset.resize = direction;
      if (direction === 'se') {
        handle.type = 'button'; handle.dataset.ui = 'resize';
        handle.setAttribute('aria-label', 'Resize Engineering agent');
        handle.title = 'Drag to resize · arrow keys resize · Shift for larger steps';
        handle.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M8 20 20 8M14 20 20 14M20 20h.01"/></svg>';
        handle.addEventListener('keydown', event => {
          if (this.mode !== 'card' || !['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown'].includes(event.key)) return;
          event.preventDefault();
          const step = event.shiftKey ? 50 : 20;
          this.resizeFrom(element.getBoundingClientRect(), 'se',
            event.key === 'ArrowRight' ? step : event.key === 'ArrowLeft' ? -step : 0,
            event.key === 'ArrowDown' ? step : event.key === 'ArrowUp' ? -step : 0);
          this.remember();
        });
      } else handle.setAttribute('aria-hidden', 'true');
      handle.addEventListener('pointerdown', event => {
        if (this.mode !== 'card' || event.button !== 0 || !event.isPrimary || this.drag) return;
        event.preventDefault(); event.stopPropagation();
        this.resizeGesture = {pointer: event.pointerId, handle, direction, startX: event.clientX,
          startY: event.clientY, box: element.getBoundingClientRect()};
        element.classList.add('resizing'); handle.setPointerCapture(event.pointerId);
      });
      handle.addEventListener('pointermove', event => {
        const gesture = this.resizeGesture;
        if (!gesture || gesture.handle !== handle || gesture.pointer !== event.pointerId) return;
        event.preventDefault();
        this.resizeFrom(gesture.box, gesture.direction, event.clientX - gesture.startX, event.clientY - gesture.startY);
      });
      const finishResize = event => {
        if (this.resizeGesture?.handle !== handle || this.resizeGesture.pointer !== event.pointerId) return;
        this.resizeGesture = null; element.classList.remove('resizing');
        if (handle.hasPointerCapture(event.pointerId)) handle.releasePointerCapture(event.pointerId);
        this.remember();
      };
      for (const event of ['pointerup', 'pointercancel', 'lostpointercapture']) handle.addEventListener(event, finishResize);
      this.resizeHandles.push(handle); element.append(handle);
    }
    this.onResize = () => {
      if (!element.hidden && this.mode !== 'fullscreen' && !this.drag && !this.resizeGesture) {
        this.place(this.layouts[this.mode] || element.getBoundingClientRect());
      }
    };
    window.addEventListener('resize', this.onResize);
    // Fit the available viewport without overwriting the user's chosen layout.
    this.resizeObserver = new ResizeObserver(() => {
      if (!element.hidden && this.mode === 'card' && !this.drag && !this.resizeGesture) this.onResize();
    });
    this.resizeObserver.observe(element);
    this.updateControls();
  }

  open() {
    this.element.hidden = false;
    this.setMode('card');
  }

  close() {
    this.element.hidden = true;
    this.onClose?.();
  }

  remember(box = this.element.getBoundingClientRect()) {
    if (this.element.hidden || this.mode === 'fullscreen') return;
    const {x, y, width, height} = box;
    this.layouts[this.mode] = {x, y, width, height};
    try { localStorage.setItem(layoutKey, JSON.stringify(this.layouts)); } catch { /* Moving still works. */ }
  }

  place(box) {
    const width = Math.min(box.width, Math.max(0, window.innerWidth - 2*margin));
    const height = Math.min(box.height, Math.max(0, window.innerHeight - 2*margin));
    const x = Math.max(margin, Math.min(box.x, window.innerWidth - width - margin));
    const y = Math.max(margin, Math.min(box.y, window.innerHeight - height - margin));
    Object.assign(this.element.style, {left: `${x}px`, top: `${y}px`, right: 'auto', bottom: 'auto',
      width: `${width}px`, height: `${height}px`});
  }

  resizeFrom(box, direction, dx, dy) {
    const style = getComputedStyle(this.element);
    const minWidth = Math.min(parseFloat(style.minWidth) || 300, window.innerWidth - 2 * margin);
    const minHeight = Math.min(parseFloat(style.minHeight) || 280, window.innerHeight - 2 * margin);
    const clamp = (value, low, high) => Math.max(low, Math.min(value, high));
    let left = box.x, top = box.y, right = box.x + box.width, bottom = box.y + box.height;
    if (direction.includes('w')) left = clamp(left + dx, margin, right - minWidth);
    if (direction.includes('e')) right = clamp(right + dx, left + minWidth, window.innerWidth - margin);
    if (direction.includes('n')) top = clamp(top + dy, margin, bottom - minHeight);
    if (direction.includes('s')) bottom = clamp(bottom + dy, top + minHeight, window.innerHeight - margin);
    this.place({x: left, y: top, width: right - left, height: bottom - top});
  }

  setMode(mode) {
    const box = this.element.getBoundingClientRect();
    const previousMode = this.mode;
    this.mode = mode;
    this.element.classList.toggle('collapsed', mode === 'pill');
    this.element.classList.toggle('maximized', mode === 'fullscreen');
    this.element.dataset.windowMode = mode;
    this.element.removeAttribute('style');
    if (mode !== 'fullscreen') {
      const size = this.element.getBoundingClientRect();
      const saved = this.layouts[mode];
      const anchor = previousMode === 'fullscreen' ? this.layouts.card || size : this.layouts[previousMode] || (box.width ? box : size);
      const preferred = mode === 'pill' ? {x: anchor.x, y: anchor.y, width: size.width, height: size.height} :
        previousMode === 'pill' ? {x: anchor.x, y: anchor.y, width: saved?.width || size.width, height: saved?.height || size.height} :
        saved || {x: anchor.x, y: anchor.y, width: size.width, height: size.height};
      this.remember(preferred);
      this.place(preferred);
    }
    this.updateControls();
    this.onMode?.(mode);
    if (mode === 'pill') this.title.focus({preventScroll: true});
  }

  updateControls() {
    this.title.setAttribute('aria-expanded', String(this.mode !== 'pill'));
    this.title.setAttribute('aria-label', this.mode === 'pill' ? 'Open Engineering agent' : 'Engineering agent');
    this.maximize.setAttribute('aria-label', this.mode === 'fullscreen' ? 'Restore floating agent card' : 'Expand agent to full screen');
    this.maximize.title = this.mode === 'fullscreen' ? 'Restore floating card' : 'Full screen';
    this.collapse.title = 'Collapse to title pill';
    for (const handle of this.resizeHandles) if (handle.tagName === 'BUTTON') handle.disabled = this.mode !== 'card';
  }

  dispose() {
    window.removeEventListener('resize', this.onResize);
    this.resizeObserver.disconnect();
  }
}
