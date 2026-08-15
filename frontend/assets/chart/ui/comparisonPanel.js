/**
 * ComparisonPanel (PHASE 13) — nút "So sánh" + popover chọn mã.
 * Hỗ trợ NHIỀU mã so sánh: mỗi mã 1 chip (màu + % + nút ×), thêm/xoá từng mã.
 */
function esc(s) {
    return String(s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

export class ComparisonPanel {
    /**
     * @param {object} deps
     *   - onAdd     (symbol) => Promise<boolean>
     *   - onRemove  (symbol) => void
     *   - onClearAll () => void
     *   - suggestions: string[]
     */
    constructor(deps) {
        this.onAdd = deps.onAdd || null;
        this.onRemove = deps.onRemove || null;
        this.onClearAll = deps.onClearAll || null;
        this.onModeChange = deps.onModeChange || null;
        this.suggestions = deps.suggestions || ['VNINDEX', 'VN30', 'TCB', 'VCB', 'ACB', 'HPG', 'FPT'];
        this.wrap = null;
        this.btn = null;
        this.pop = null;
        this.input = null;
        this.chipWrap = null;
        this.modeBtns = {};
        this._outside = null;
    }

    create() {
        const wrap = document.createElement('div');
        wrap.className = 'chart-cmp-wrap';

        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'an-btn cmp-btn';
        btn.innerHTML = '<i class="fa-solid fa-code-compare"></i> <span class="btn-label">So sánh</span>';
        btn.addEventListener('click', (e) => { e.stopPropagation(); this.toggle(); });

        const pop = document.createElement('div');
        pop.className = 'cmp-pop';
        pop.style.display = 'none';

        const input = document.createElement('input');
        input.type = 'text';
        input.className = 'cmp-input';
        input.placeholder = 'Mã: VNINDEX, TCB...';
        input.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') { e.preventDefault(); this.pick(input.value.trim()); }
        });

        const sug = document.createElement('div');
        sug.className = 'cmp-sug';
        for (const s of this.suggestions) {
            const c = document.createElement('button');
            c.type = 'button';
            c.className = 'cmp-sug-chip';
            c.textContent = s;
            c.addEventListener('click', () => this.pick(s));
            sug.appendChild(c);
        }

        const clear = document.createElement('button');
        clear.type = 'button';
        clear.className = 'cmp-clear';
        clear.textContent = 'Xoá tất cả so sánh';
        clear.addEventListener('click', () => { this.clearAll(); });

        // Chế độ hiển thị: % tương đối / giá tuyệt đối
        const mode = document.createElement('div');
        mode.className = 'cmp-mode';
        const ml = document.createElement('span');
        ml.className = 'cmp-mode-label';
        ml.textContent = 'Dạng: ';
        mode.appendChild(ml);
        for (const [m, label] of [['pct', '%'], ['abs', 'Giá']]) {
            const mb = document.createElement('button');
            mb.type = 'button';
            mb.className = 'cmp-mode-btn' + (m === 'pct' ? ' active' : '');
            mb.textContent = label;
            mb.title = m === 'pct' ? 'So sánh theo % thay đổi' : 'So sánh theo giá tuyệt đối';
            mb.dataset.mode = m;
            mb.addEventListener('click', () => { if (this.onModeChange) this.onModeChange(m); });
            this.modeBtns[m] = mb;
            mode.appendChild(mb);
        }

        pop.appendChild(input);
        pop.appendChild(sug);
        pop.appendChild(mode);
        pop.appendChild(clear);

        const chipWrap = document.createElement('div');
        chipWrap.className = 'cmp-chip-wrap';

        wrap.appendChild(btn);
        wrap.appendChild(pop);
        wrap.appendChild(chipWrap);

        this.wrap = wrap;
        this.btn = btn;
        this.pop = pop;
        this.input = input;
        this.chipWrap = chipWrap;

        this._outside = (e) => { if (this.wrap && !this.wrap.contains(e.target)) this.close(); };
        document.addEventListener('pointerdown', this._outside);
        return wrap;
    }

    async pick(symbol) {
        if (!symbol || !this.onAdd) return;
        const ok = await this.onAdd(symbol);
        if (ok) this.close();
    }

    clearAll() {
        if (this.onClearAll) this.onClearAll();
        this.close();
    }

    setModeActive(mode) {
        for (const m of ['pct', 'abs']) {
            if (this.modeBtns[m]) this.modeBtns[m].classList.toggle('active', m === mode);
        }
    }

    /** items = [{ symbol, color, text, cls }] */
    setResult(items) {
        this.chipWrap.innerHTML = '';
        for (const it of (items || [])) {
            const chip = document.createElement('span');
            chip.className = 'cmp-chip';
            const dot = it.color ? '<i class="cmp-dot" style="background:' + it.color + '"></i>' : '<i class="fa-solid fa-code-compare"></i>';
            chip.innerHTML = dot + ' ' + esc(it.symbol) +
                ' <b class="' + (it.cls || '') + '">' + esc(it.text || '--') + '</b> ' +
                '<button type="button" class="cmp-chip-x" title="Xoá ' + esc(it.symbol) + '">×</button>';
            chip.querySelector('.cmp-chip-x').addEventListener('click', () => { if (this.onRemove) this.onRemove(it.symbol); });
            this.chipWrap.appendChild(chip);
        }
    }

    setCleared() { this.chipWrap.innerHTML = ''; }

    toggle() { this.pop.style.display === 'none' ? this.open() : this.close(); }
    open() {
        this.pop.style.display = 'block';
        if (this.input) setTimeout(() => this.input.focus(), 0);
    }
    close() { if (this.pop) this.pop.style.display = 'none'; }

    destroy() {
        if (this._outside) document.removeEventListener('pointerdown', this._outside);
        if (this.wrap) this.wrap.remove();
    }
}
