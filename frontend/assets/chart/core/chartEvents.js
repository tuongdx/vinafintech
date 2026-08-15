/**
 * ChartEvents — event bus (pub/sub) nhỏ để tách các module.
 * DATA / CALCULATION / STATE / RENDERING / INTERACTION giao tiếp qua sự kiện.
 */
export class ChartEvents {
    constructor() {
        this._handlers = new Map();
    }

    on(event, fn) {
        if (!this._handlers.has(event)) this._handlers.set(event, []);
        this._handlers.get(event).push(fn);
        return () => this.off(event, fn);
    }

    off(event, fn) {
        const list = this._handlers.get(event);
        if (!list) return;
        const i = list.indexOf(fn);
        if (i !== -1) list.splice(i, 1);
    }

    emit(event, payload) {
        const list = this._handlers.get(event);
        if (!list) return;
        for (const fn of [...list]) {
            try { fn(payload); } catch (e) { /* không để 1 handler làm hỏng bus */ }
        }
    }

    clear() {
        this._handlers.clear();
    }
}
