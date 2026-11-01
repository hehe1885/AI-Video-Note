// Keep ISO date values and change events used by history filters; own the popup
// so its corners, colours and focus states follow the application theme.
(() => {
    function init() {
        const inputs = ['historyStartDate', 'historyEndDate'].map(id => document.getElementById(id)).filter(Boolean);
        if (!inputs.length) return;
        const popup = document.createElement('div');
        popup.className = 'history-calendar';
        popup.hidden = true;
        popup.setAttribute('role', 'dialog');
        popup.id = 'historyCalendar';
        document.body.append(popup);
        let active = null, month = null, cursor = null;
        const iso = date => `${date.getFullYear()}-${String(date.getMonth()+1).padStart(2,'0')}-${String(date.getDate()).padStart(2,'0')}`;
        const parse = value => /^\d{4}-\d{2}-\d{2}$/.test(value) ? new Date(`${value}T12:00:00`) : new Date();
        const locale = () => document.documentElement.lang.startsWith('en') ? 'en-US' : 'zh-CN';
        const tr = text => window.AIVideoNoteI18n?.translate(text) || text;
        function close(focus = false) {
            if (!active) return;
            const input = active;
            active = null;
            popup.hidden = true;
            input.setAttribute('aria-expanded','false');
            if (focus) input.focus();
        }
        function select(value) {
            const input = active;
            input.value = value;
            close(true);
            input.dispatchEvent(new Event('change', {bubbles:true}));
        }
        function button(text, action, label) {
            const node = document.createElement('button');
            node.type = 'button';
            node.textContent = text;
            if (label) node.setAttribute('aria-label', label);
            node.addEventListener('click', action);
            return node;
        }
        function position() {
            if (!active) return;
            const rect = active.getBoundingClientRect();
            popup.style.left = `${Math.max(8, Math.min(rect.left, window.innerWidth-popup.offsetWidth-8))}px`;
            const below = rect.bottom+6;
            popup.style.top = `${below}px`;
            popup.style.maxHeight = `${Math.max(0, window.innerHeight-below-8)}px`;
        }
        function render(focusDay = false) {
            popup.replaceChildren();
            popup.setAttribute('aria-label', active.closest('label')?.querySelector('span')?.textContent || tr('选择日期'));
            const header = document.createElement('div');
            header.className = 'history-calendar-header';
            const title = document.createElement('strong');
            title.textContent = new Intl.DateTimeFormat(locale(), {year:'numeric',month:'long'}).format(month);
            title.setAttribute('aria-live','polite');
            const move = delta => {month = new Date(month.getFullYear(),month.getMonth()+delta,1,12); cursor = new Date(month); render(true);};
            header.append(title, button('‹',()=>move(-1),tr('上个月')), button('›',()=>move(1),tr('下个月')));
            const grid = document.createElement('div');
            grid.className = 'history-calendar-grid';
            for (let day = 0; day < 7; day++) {
                const label = document.createElement('span');
                label.className = 'history-calendar-weekday';
                label.textContent = new Intl.DateTimeFormat(locale(),{weekday:'short'}).format(new Date(2024,0,1+day,12));
                grid.append(label);
            }
            const start = new Date(month.getFullYear(),month.getMonth(),1,12);
            start.setDate(start.getDate()-(start.getDay()+6)%7);
            let focus;
            for (let i = 0; i < 42; i++) {
                const date = new Date(start); date.setDate(start.getDate()+i);
                const value = iso(date);
                const node = button(String(date.getDate()),()=>select(value), new Intl.DateTimeFormat(locale(),{dateStyle:'full'}).format(date));
                node.classList.toggle('outside-month',date.getMonth()!==month.getMonth());
                node.classList.toggle('selected',value===active.value);
                if (value===active.value) node.setAttribute('aria-pressed','true');
                if (value===iso(new Date())) node.setAttribute('aria-current','date');
                node.tabIndex = value===iso(cursor) ? 0 : -1;
                if (node.tabIndex===0) focus=node;
                node.addEventListener('keydown',event=>{
                    const shifts = {ArrowLeft:-1,ArrowRight:1,ArrowUp:-7,ArrowDown:7};
                    if (!(event.key in shifts) && !['Home','End','PageUp','PageDown'].includes(event.key)) return;
                    event.preventDefault();
                    cursor = new Date(date);
                    if (event.key==='PageUp' || event.key==='PageDown') {
                        const wanted = cursor.getDate();
                        cursor.setDate(1); cursor.setMonth(cursor.getMonth()+(event.key==='PageUp'?-1:1));
                        cursor.setDate(Math.min(wanted,new Date(cursor.getFullYear(),cursor.getMonth()+1,0).getDate()));
                    } else cursor.setDate(cursor.getDate()+(event.key==='Home'?-(cursor.getDay()+6)%7:event.key==='End'?6-(cursor.getDay()+6)%7:shifts[event.key]));
                    month = new Date(cursor.getFullYear(),cursor.getMonth(),1,12);
                    render(true);
                });
                grid.append(node);
            }
            const footer = document.createElement('div');
            footer.className = 'history-calendar-footer';
            footer.append(button(tr('清除'),()=>select('')),button(tr('今天'),()=>select(iso(new Date()))));
            popup.append(header,grid,footer);
            position();
            if (focusDay) focus?.focus();
        }
        function open(input) {
            if (active===input) {close(true); return;}
            close(); active=input;
            cursor=parse(input.value);
            month=new Date(cursor.getFullYear(),cursor.getMonth(),1,12);
            input.setAttribute('aria-expanded','true');
            popup.hidden=false; render(true);
        }
        for (const input of inputs) {
            input.setAttribute('aria-haspopup','dialog');
            input.setAttribute('aria-controls',popup.id);
            input.setAttribute('aria-expanded','false');
            input.addEventListener('click',event=>{event.preventDefault();open(input);});
            input.addEventListener('keydown',event=>{
                if (event.key==='Enter' || event.key===' ' || event.key==='F4' || (event.altKey && event.key==='ArrowDown')) {event.preventDefault();open(input);}
            });
        }
        document.addEventListener('pointerdown',event=>{if(active && !popup.contains(event.target) && !inputs.includes(event.target))close();});
        document.addEventListener('focusin',event=>{if(active && !popup.contains(event.target) && event.target!==active)close();});
        document.addEventListener('keydown',event=>{if(active && event.key==='Escape'){event.preventDefault();close(true);}});
        window.addEventListener('resize',()=>close());
        window.addEventListener('scroll',()=>close(),true);
        window.addEventListener('ui-language-changed',()=>{if(active)render();});
    }
    if(document.readyState==='loading') document.addEventListener('DOMContentLoaded',init); else init();
})();
