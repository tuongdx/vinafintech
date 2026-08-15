/**
 * DrawingManager — state của toàn bộ drawing.
 * Drawing được lưu theo (time, price) — KHÔNG theo pixel → chính xác khi
 * resize/zoom/đổi màn hình. Hỗ trợ visible/locked/selected + undo/redo.
 */
let UID = 0;

export class DrawingManager {
    constructor(options = {}) {
        this.symbol = options.symbol || '';
        this.timeframe = options.timeframe || '1D';
        this.list = [];
        this.selectedId = null;
        this.undoStack = [];
        this.redoStack = [];
        this.maxHistory = options.maxHistory || 100;
        this.onChange = options.onChange || null;
    }

    setContext(symbol, timeframe) {
        this.symbol = String(symbol || '').toUpperCase();
        this.timeframe = timeframe || '1D';
        this._notify();
    }

    getVisible() {
        return this.list.filter((d) => d.symbol === this.symbol && d.timeframe === this.timeframe);
    }

    getAll() { return this.list; }

    get(id) { return this.list.find((d) => d.id === id) || null; }

    // Khôi phục list từ localStorage (PHASE 6) — không gọi _notify để tránh ghi lại.
    loadDrawings(list) {
        if (!Array.isArray(list)) return;
        this.list = JSON.parse(JSON.stringify(list));
        if (this.selectedId && !this.get(this.selectedId)) this.selectedId = null;
    }

    getSelected() { return this.selectedId ? this.get(this.selectedId) : null; }

    select(id) {
        this.selectedId = id || null;
        this._notify();
        return this.getSelected();
    }

    add(drawing) {
        this._snapshot();
        const d = {
            id: 'd_' + (++UID) + '_' + Date.now().toString(36),
            type: drawing.type,
            symbol: this.symbol,
            timeframe: this.timeframe,
            points: (drawing.points || []).map((p) => ({ time: Number(p.time), price: Number(p.price) })),
            style: Object.assign({ color: '#2962ff', width: 1, opacity: 1 }, drawing.style || {}),
            visible: drawing.visible !== false,
            locked: !!drawing.locked,
            text: drawing.text || '',
        };
        this.list.push(d);
        this.selectedId = d.id;
        this._notify();
        return d;
    }

    update(id, patch) {
        const d = this.get(id);
        if (!d) return;
        this._snapshot();
        if (patch.points) d.points = patch.points.map((p) => ({ time: Number(p.time), price: Number(p.price) }));
        if (patch.style) Object.assign(d.style, patch.style);
        if (patch.visible !== undefined) d.visible = !!patch.visible;
        if (patch.locked !== undefined) d.locked = !!patch.locked;
        if (patch.text !== undefined) d.text = String(patch.text);
        this._notify();
    }

    remove(id) {
        const d = this.get(id);
        if (!d) return;
        this._snapshot();
        this.list = this.list.filter((x) => x.id !== id);
        if (this.selectedId === id) this.selectedId = null;
        this._notify();
    }

    removeSelected() { if (this.selectedId) this.remove(this.selectedId); }

    setVisible(id, v) { const d = this.get(id); if (d) this.update(id, { visible: v }); }
    setLocked(id, v) { const d = this.get(id); if (d) this.update(id, { locked: v }); }

    clearAll() {
        if (!this.list.length) return;
        this._snapshot();
        this.list = [];
        this.selectedId = null;
        this._notify();
    }

    setAllVisible(v) {
        this._snapshot();
        this.list.forEach((d) => { d.visible = v; });
        this._notify();
    }

    // ---------- undo / redo ----------
    undo() {
        if (!this.undoStack.length) return false;
        this.redoStack.push(this._serialize());
        this.list = this._deserialize(this.undoStack.pop());
        if (this.selectedId && !this.get(this.selectedId)) this.selectedId = null;
        this._notify();
        return true;
    }

    redo() {
        if (!this.redoStack.length) return false;
        this.undoStack.push(this._serialize());
        this.list = this._deserialize(this.redoStack.pop());
        if (this.selectedId && !this.get(this.selectedId)) this.selectedId = null;
        this._notify();
        return true;
    }

    _snapshot() {
        this.undoStack.push(this._serialize());
        if (this.undoStack.length > this.maxHistory) this.undoStack.shift();
        this.redoStack = [];
    }

    // Batch: dùng khi kéo handle/body — snapshot 1 lần, cập nhật liên tục
    // không ghi thêm vào undo stack (tránh 1 drag = 100 lần undo).
    beginSnapshot() { this._snapshot(); }

    applyPoints(id, points) {
        const d = this.get(id);
        if (!d) return;
        d.points = points.map((p) => ({ time: Number(p.time), price: Number(p.price) }));
        this._notify();
    }

    _serialize() { return JSON.parse(JSON.stringify(this.list)); }
    _deserialize(arr) { return JSON.parse(JSON.stringify(arr || [])); }

    _notify() {
        if (this.onChange) {
            try { this.onChange(this.list); } catch (e) { /* ignore */ }
        }
    }
}
