// Visual feedback only: native controls and task behavior remain unchanged.
(() => {
    const statuses = new Map();
    const reduced = () => !!window.matchMedia?.('(prefers-reduced-motion: reduce)')?.matches;

    function prepareTasks(tasks) {
        const visible = new Set(tasks.map(task => task.task_id));
        for (const id of statuses.keys()) if (!visible.has(id)) statuses.delete(id);
    }

    function decorateTask(row, badge, task, index) {
        const previous = statuses.get(task.task_id);
        if (!reduced()) {
            if (!statuses.has(task.task_id)) {
                row.classList.add('motion-new-task');
                row.style.setProperty('--motion-order', String(Math.min(index, 4)));
            } else if (previous !== task.status) {
                badge.classList.add('motion-state-change');
            }
        }
        statuses.set(task.task_id, task.status);
    }

    function dismissToast(toast) {
        if (reduced()) {
            toast.remove();
            return;
        }
        toast.classList.add('motion-toast-out');
        window.setTimeout(() => toast.remove(), 180);
    }

    const panels = new WeakMap();
    const visualProperties = [
        'display', 'box-sizing', 'width', 'height', 'min-width', 'min-height', 'max-width', 'max-height',
        'margin', 'padding', 'border', 'border-radius', 'background', 'box-shadow', 'color',
        'font', 'line-height', 'letter-spacing', 'text-align', 'white-space', 'word-break', 'overflow-wrap',
        'flex', 'flex-direction', 'flex-wrap', 'align-items', 'justify-content', 'gap',
        'grid-template-columns', 'grid-template-rows', 'grid-column', 'overflow', 'visibility'
    ];
    function snapshotLayer(inner) {
        if (!inner?.cloneNode || !window.getComputedStyle) return null;
        const ghost = inner.cloneNode(true);
        const sourceNodes = [inner, ...inner.querySelectorAll('*')];
        const ghostNodes = [ghost, ...ghost.querySelectorAll('*')];
        sourceNodes.forEach((source, index) => {
            const copy = ghostNodes[index];
            // Class styles survive cloning. Only ID-dependent styles and hidden rows need freezing.
            if (index === 0 || source.hasAttribute('id') || source.matches('.note-settings-row')) {
                const computed = window.getComputedStyle(source);
                for (const property of visualProperties) copy.style.setProperty(property, computed.getPropertyValue(property));
            }
            // A visual copy must not join radio groups, duplicate IDs, or submit form data.
            copy.removeAttribute('id');
            copy.removeAttribute('name');
            copy.style.setProperty('animation', 'none');
            copy.style.setProperty('transition', 'none');
        });
        ghost.classList.add('source-mode-ghost');
        ghost.style.background = 'var(--surface)';
        ghost.setAttribute('aria-hidden', 'true');
        ghost.setAttribute('data-no-i18n', '');
        ghost.inert = true;
        return ghost;
    }
    // Analytic critically damped spring: stable at different frame rates, no oscillatory bounce.
    function advanceSpring(spring, dt) {
        const omega = 28;
        const displacement = spring.value - spring.target;
        const coefficient = spring.velocity + omega * displacement;
        const decay = Math.exp(-omega * dt);
        spring.value = spring.target + (displacement + coefficient * dt) * decay;
        spring.velocity = (spring.velocity - omega * coefficient * dt) * decay;
    }
    function cancelPanel(panel) {
        const state = panel && panels.get(panel);
        if (!state) return;
        panels.delete(panel);
        window.cancelAnimationFrame(state.frame);
        Object.assign(panel.style, state.styles);
        Object.assign(state.inner.style, state.innerStyles);
        state.ghost?.remove();
        panel.inert = state.inert;
    }
    function switchPanel(panel, update, options = {}) {
        const topAnchored = options.anchor === 'top';
        const panelRect = panel?.getBoundingClientRect?.();
        const renderedAnchor = topAnchored ? undefined : panel?.querySelector?.('.source-mode-inner .source-controls')?.getBoundingClientRect().top;
        const previous = panel && panels.get(panel);
        const heightVelocity = previous?.height?.velocity || 0;
        const anchorVelocity = previous?.anchor?.velocity || 0;
        cancelPanel(panel);
        const inner = panel?.querySelector?.('.source-mode-inner');
        if (!panel || !inner || reduced() || !window.requestAnimationFrame || !window.cancelAnimationFrame || !('inert' in panel)) {
            update();
            return;
        }
        const state = { inert: panel.inert, inner, frame: null, ghost: null,
            styles: { height: panel.style.height, overflow: panel.style.overflow },
            innerStyles: { transform: inner.style.transform, willChange: inner.style.willChange } };
        panels.set(panel, state);
        const current = () => panels.get(panel) === state;
        let updated = false;
        try {
            const oldTop = inner.querySelector('.source-controls')?.getBoundingClientRect().top;
            const oldAnchor = topAnchored ? 0 : Number.isFinite(oldTop) ? oldTop - panel.getBoundingClientRect().top : panelRect.height;
            state.ghost = snapshotLayer(inner);
            if (topAnchored && state.ghost) state.ghost.style.background = 'var(--surface-deep)';
            panel.inert = true;
            panel.style.height = `${panelRect.height}px`;
            panel.style.overflow = 'hidden';
            updated = true;
            update();
            const nextHeight = inner.getBoundingClientRect().height;
            const newTop = inner.querySelector('.source-controls')?.getBoundingClientRect().top;
            const newAnchor = topAnchored ? 0 : Number.isFinite(newTop) ? newTop - panel.getBoundingClientRect().top : nextHeight;
            state.height = { value: panelRect.height, target: nextHeight, velocity: heightVelocity };
            state.anchor = { value: Number.isFinite(renderedAnchor) ? renderedAnchor - panelRect.top : oldAnchor,
                target: newAnchor, velocity: anchorVelocity };
            if (state.ghost) {
                state.ghost.style.willChange = 'transform, opacity';
                panel.appendChild(state.ghost);
            }
            inner.style.willChange = 'transform';
            const paint = blend => {
                panel.style.height = `${Math.max(0, state.height.value)}px`;
                inner.style.transform = `translateY(${state.anchor.value - newAnchor}px)`;
                if (state.ghost) {
                    state.ghost.style.transform = `translateY(${state.anchor.value - oldAnchor}px)`;
                    state.ghost.style.opacity = String(1 - blend);
                }
            };
            const settled = spring => Math.abs(spring.value - spring.target) < .2 && Math.abs(spring.velocity) < 3;
            let firstTime = window.performance?.now?.() ?? null;
            let lastTime = firstTime;
            paint(0);
            const tick = now => {
                if (!current()) return;
                try {
                    if (reduced()) { cancelPanel(panel); return; }
                    if (firstTime === null) firstTime = now;
                    const dt = lastTime === null ? 1 / 60 : Math.min(Math.max((now - lastTime) / 1000, 0), .064);
                    lastTime = now;
                    advanceSpring(state.height, dt);
                    advanceSpring(state.anchor, dt);
                    const progress = Math.min(1, (now - firstTime) / 180);
                    paint(progress * progress * (3 - 2 * progress));
                    if (progress === 1 && state.ghost) { state.ghost.remove(); state.ghost = null; }
                    if ((settled(state.height) && settled(state.anchor) && progress === 1) || now - firstTime > 1500) {
                        cancelPanel(panel);
                    } else state.frame = window.requestAnimationFrame(tick);
                } catch (_error) { cancelPanel(panel); }
            };
            state.frame = window.requestAnimationFrame(tick);
        } catch (_error) {
            try { if (!updated) update(); } finally { if (current()) cancelPanel(panel); }
        }
    }

    // Exactly three durations; use the natural content height for both directions.
    function disclosureDuration(height) {
        return height <= 500 ? 220 : height <= 850 ? 350 : 490;
    }
    function prepareDisclosure(details) {
        if (reduced() || !details?.querySelector || !window.getComputedStyle) return;
        const summary = details.querySelector(':scope > summary');
        if (!summary) return;
        // Reveal layout inside the clipped box without changing its animated
        // height or the native open state. No cloned controls or forced toggle.
        details.classList.add('motion-measure-disclosure');
        try {
            const boxes = [...details.children].filter(child => child !== summary
                && window.getComputedStyle(child).display !== 'none').map(child => {
                const rect = child.getBoundingClientRect();
                const style = window.getComputedStyle(child);
                return {top: rect.top - (parseFloat(style.marginTop) || 0),
                    bottom: rect.bottom + (parseFloat(style.marginBottom) || 0)};
            });
            const height = boxes.length ? Math.max(0,
                Math.max(...boxes.map(box => box.bottom)) - Math.min(...boxes.map(box => box.top))) : 0;
            details.style.setProperty('--disclosure-duration', `${disclosureDuration(height)}ms`);
        } finally { details.classList.remove('motion-measure-disclosure'); }
    }
    function initDisclosureTiers() {
        const sidebar = document.querySelector('.sidebar');
        if (!sidebar || sidebar.dataset.disclosureTiersReady) return;
        sidebar.dataset.disclosureTiersReady = 'true';
        sidebar.addEventListener('click', event => {
            const summary = event.target.closest?.('summary');
            const details = summary?.parentElement;
            if (!details?.matches('.config-section') || !sidebar.contains(details)) return;
            if (event.target.closest('a, button, input, select, textarea')) return;
            prepareDisclosure(details);
        }, {capture: true});
    }

    function initSidebarScrollbarExit() {
        const sidebar = document.querySelector('.sidebar');
        if (!sidebar || !window.ResizeObserver || sidebar.dataset.scrollbarExitReady) return;
        sidebar.dataset.scrollbarExitReady = 'true';
        let lastThumb = null, fading = null, fallbackTimer = null;
        const clearFade = () => {
            if (fallbackTimer !== null) window.clearTimeout?.(fallbackTimer);
            fallbackTimer = null;
            fading?.remove();
            fading = null;
        };
        const inspect = () => {
            const height = sidebar.clientHeight;
            const range = sidebar.scrollHeight - height;
            if (range > 1 && height > 0) {
                clearFade();
                const rect = sidebar.getBoundingClientRect();
                const style = window.getComputedStyle(sidebar);
                const borderRight = parseFloat(style.borderRightWidth) || 0;
                const borderLeft = parseFloat(style.borderLeftWidth) || 0;
                const gutter = Math.max(6, sidebar.offsetWidth - sidebar.clientWidth - borderRight - borderLeft);
                const width = Math.min(6, Math.max(3, gutter - 2));
                const thumbHeight = Math.min(height, Math.max(24, height * height / sidebar.scrollHeight));
                lastThumb = {
                    left: rect.right - borderRight - gutter + (gutter - width) / 2,
                    top: rect.top + sidebar.clientTop + (height - thumbHeight) * Math.max(0, Math.min(1, sidebar.scrollTop / range)),
                    width, height: thumbHeight
                };
                return;
            }
            if (!lastThumb) return;
            const geometry = lastThumb;
            lastThumb = null;
            if (reduced() || window.matchMedia?.('(forced-colors: active)')?.matches) return;
            clearFade();
            const ghost = document.createElement('div');
            ghost.className = 'sidebar-scrollbar-exit';
            ghost.setAttribute('aria-hidden', 'true');
            ghost.setAttribute('data-no-i18n', '');
            for (const [property, value] of Object.entries(geometry)) ghost.style[property] = `${value}px`;
            const finish = () => { if (fading === ghost) clearFade(); };
            ghost.addEventListener('animationend', finish, {once:true});
            document.body.appendChild(ghost);
            fading = ghost;
            fallbackTimer = window.setTimeout(finish, 320);
        };
        const observer = new window.ResizeObserver(inspect);
        observer.observe(sidebar);
        for (const child of sidebar.children) observer.observe(child);
        sidebar.addEventListener('scroll', inspect, {passive:true});
        window.addEventListener('resize', inspect, {passive:true});
        inspect();
    }
    if (typeof document !== 'undefined') document.addEventListener?.('DOMContentLoaded', () => {
        initSidebarScrollbarExit();
        initDisclosureTiers();
    });

    window.AIVideoNoteMotion = { prepareTasks, decorateTask, dismissToast, switchPanel, cancelPanel, initSidebarScrollbarExit, disclosureDuration, prepareDisclosure, initDisclosureTiers, scrollBehavior: () => reduced() ? 'auto' : 'smooth' };
})();
