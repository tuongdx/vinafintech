/**
 * VinaFintechDatafeed — TradingView Advanced Charts Datafeed.
 *
 * Kết nối TradingView Advanced Charts (charting_library) với dữ liệu VinaFintech:
 *
 *   TradingView Advanced Charts
 *        |  (Datafeed API)
 *        v
 *   VinaFintechDatafeed (file này)
 *        |  (fetch /api/chart/*)
 *        v
 *   VinaFintech Backend (FastAPI -> chart_service -> vnstock_service -> Simplize)
 *        |
 *        v
 *   Dữ liệu OHLCV của VinaFintech
 *
 * KHÔNG gọi thẳng Simplize/TradingView từ frontend. Mọi dữ liệu đi qua backend
 * VinaFintech để đảm bảo không lộ nguồn dữ liệu và không phụ thuộc API TradingView.
 *
 * Timeframe được hỗ trợ (chỉ hiển thị những gì VinaFintech thực sự có):
 *   - 1D: nến daily (dữ liệu daily của VinaFintech/Simplize)
 *   - 1W, 1M: gộp từ nến daily (backend aggregate, dữ liệu thực)
 *   - 1m/5m/15m/30m/1H/4H: KHÔNG hỗ trợ (VinaFintech chưa có lịch sử intraday OHLCV)
 *
 * Realtime: VinaFintech chưa có nguồn OHLCV realtime -> subscribeBars hoạt động chế độ
 * historical. Đã thiết kế sẵn cơ chế polling (bật bằng REALTIME_ENABLED=true)
 * để sau này nối realtime thật mà không cần sửa lại kiến trúc.
 */
(function (global) {
  'use strict';

  var API_BASE = '/api/chart';

  // Bật realtime polling (chưa có nguồn thật -> mặc định TẮT).
  // Khi VinaFintech có nguồn OHLCV realtime, đổi thành true và cập nhật updateLastBar.
  var REALTIME_ENABLED = false;
  var REALTIME_POLL_MS = 5000;

  function toQuery(params) {
    var parts = [];
    for (var key in params) {
      if (!Object.prototype.hasOwnProperty.call(params, key)) continue;
      var value = params[key];
      if (value === undefined || value === null || value === '') continue;
      parts.push(encodeURIComponent(key) + '=' + encodeURIComponent(String(value)));
    }
    return parts.length ? '?' + parts.join('&') : '';
  }

  function fetchJson(path, params) {
    return fetch(API_BASE + path + toQuery(params), { cache: 'no-store' }).then(function (res) {
      if (!res.ok) {
        return res.json().catch(function () { return {}; }).then(function (body) {
          var err = new Error('HTTP ' + res.status + ': ' + (body && body.detail ? body.detail : res.statusText));
          err.status = res.status;
          throw err;
        });
      }
      return res.json();
    });
  }

  function VinaFintechDatafeed(options) {
    options = options || {};
    this._apiBase = options.apiBase || API_BASE;
    this._realtimeEnabled = options.realtimeEnabled === true;
    this._realtimeTimer = {};
    this._realtimeSubs = {};
  }

  // ---------------------------------------------------------------------------
  // onReady: cấu hình chart (chỉ báo, timeframe, sàn...)
  // ---------------------------------------------------------------------------
  VinaFintechDatafeed.prototype.onReady = function (callback) {
    var self = this;
    fetchJson('/config', {}).then(function (config) {
      self._config = config || {};
      self._realtimeEnabled = config.realtime === true || self._realtimeEnabled;
      if (typeof callback === 'function') callback(config);
    }).catch(function () {
      // Nếu backend lỗi, vẫn báo config mặc định tối thiểu để chart không chết
      if (typeof callback === 'function') {
        callback({
          supports_search: true,
          supports_group_request: false,
          supports_marks: false,
          supports_timescale_marks: false,
          supports_time: true,
          exchanges: [{ value: '', name: 'HOSE', desc: 'HOSE' }],
          symbols_types: [{ name: 'Cổ phiếu', value: 'stock' }],
          supported_resolutions: ['1D', '1W', '1M'],
          timezone: 'Asia/Ho_Chi_Minh',
          session: '0900-1500',
          has_intraday: false,
        });
      }
    });
  };

  // ---------------------------------------------------------------------------
  // searchSymbols: tìm mã cổ phiếu (dữ liệu VinaFintech qua /api/chart/search)
  // ---------------------------------------------------------------------------
  VinaFintechDatafeed.prototype.searchSymbols = function (userInput, exchange, symbolType, onResult) {
    var query = String(userInput || '').trim();
    if (!query) {
      if (typeof onResult === 'function') onResult([]);
      return;
    }
    fetchJson('/search', { query: query, limit: 20 }).then(function (rows) {
      if (typeof onResult === 'function') onResult(Array.isArray(rows) ? rows : []);
    }).catch(function () {
      if (typeof onResult === 'function') onResult([]);
    });
  };

  // ---------------------------------------------------------------------------
  // resolveSymbol: lấy metadata symbol từ VinaFintech (/api/chart/symbols)
  // ---------------------------------------------------------------------------
  VinaFintechDatafeed.prototype.resolveSymbol = function (symbolName, onSymbolResolvedCallback, onResolveErrorCallback) {
    fetchJson('/symbols', { symbol: symbolName }).then(function (info) {
      if (!info || info.valid === false) {
        if (typeof onResolveErrorCallback === 'function') {
          onResolveErrorCallback(info && info.message ? info.message : 'Không tìm thấy mã cổ phiếu: ' + symbolName);
        }
        return;
      }
      if (typeof onSymbolResolvedCallback === 'function') {
        onSymbolResolvedCallback({
          name: info.symbol,
          full_name: info.full_name || info.symbol,
          ticker: info.ticker || info.symbol,
          description: info.description || info.symbol,
          type: info.type || 'stock',
          session: info.session || '0900-1500',
          timezone: info.timezone || 'Asia/Ho_Chi_Minh',
          exchange: info.exchange || 'HOSE',
          listed_exchange: info.listed_exchange || info.exchange || 'HOSE',
          minmov: info.minmov != null ? info.minmov : 1,
          minmove2: info.minmove2 || 0,
          fractional: !!info.fractional,
          pricescale: info.pricescale || 100,
          has_intraday: !!info.has_intraday,
          has_seconds: !!info.has_seconds,
          has_daily: info.has_daily !== false,
          has_empty_bars: !!info.has_empty_bars,
          supported_resolutions: info.supported_resolutions || ['1D', '1W', '1M'],
          volume_precision: info.volume_precision || 0,
          data_status: info.data_status || 'delayed',
          currency_code: info.currency_code || 'VND',
        });
      }
    }).catch(function (error) {
      if (typeof onResolveErrorCallback === 'function') onResolveErrorCallback(error && error.message ? error.message : 'Lỗi resolve symbol');
    });
  };

  // ---------------------------------------------------------------------------
  // getBars: lấy OHLCV theo resolution + from/to (phân trang lịch sử)
  // Hỗ trợ cả 2 dạng gọi: legacy (from, to) và periodParams (countback).
  // ---------------------------------------------------------------------------
  VinaFintechDatafeed.prototype.getBars = function (symbolInfo, resolution, periodParams, onHistoryCallback, onErrorCallback, firstDataRequest) {
    var from, to, countback;

    // Dạng 1 (mới): getBars(symbolInfo, resolution, periodParams, ...)
    // Dạng 2 (cũ):  getBars(symbolInfo, resolution, from, to, ...)
    if (periodParams && typeof periodParams === 'object' && typeof periodParams.from === 'number') {
      from = periodParams.from;
      to = periodParams.to;
      countback = periodParams.countback;
    } else {
      from = periodParams;            // from
      to = arguments[3];              // to
      onHistoryCallback = arguments[4];
      onErrorCallback = arguments[5];
      firstDataRequest = arguments[6];
      countback = 0;
    }

    var symbol = (symbolInfo && (symbolInfo.ticker || symbolInfo.name)) || '';
    var interval = String(resolution || '1D').toUpperCase();

    fetchJson('/history', {
      symbol: symbol,
      interval: interval,
      from: Math.floor(from || 0),
      to: Math.floor(to || 0),
      countback: Math.floor(countback || 0),
    }).then(function (data) {
      if (!data || data.valid === false) {
        if (typeof onErrorCallback === 'function') onErrorCallback(data && data.message ? data.message : 'Lỗi lấy dữ liệu biểu đồ');
        return;
      }

      var bars = Array.isArray(data.bars) ? data.bars.map(function (bar) {
        return {
          time: Number(bar.time),
          open: Number(bar.open),
          high: Number(bar.high),
          low: Number(bar.low),
          close: Number(bar.close),
          volume: Number(bar.volume),
        };
      }) : [];

      // Sắp xếp tăng dần theo thời gian + loại bỏ bar lỗi (bảo vệ thêm ở frontend)
      bars.sort(function (a, b) { return a.time - b.time; });
      bars = bars.filter(function (b) {
        return Number.isFinite(b.time) && Number.isFinite(b.open) && Number.isFinite(b.high) &&
               Number.isFinite(b.low) && Number.isFinite(b.close) && b.high >= b.low;
      });

      var meta = { noData: data.noData === true || bars.length === 0 };
      if (data.nextTime) {
        meta.nextTime = Number(data.nextTime);
      }

      if (typeof onHistoryCallback === 'function') onHistoryCallback(bars, meta);
    }).catch(function (error) {
      if (typeof onErrorCallback === 'function') onErrorCallback(error && error.message ? error.message : 'Không tải được dữ liệu biểu đồ');
    });
  };

  // ---------------------------------------------------------------------------
  // subscribeBars / unsubscribeBars: realtime.
  // VinaFintech hiện chưa có nguồn OHLCV realtime -> ở chế độ historical (không đẩy
  // dữ liệu). Khi có nguồn thật: bật REALTIME_ENABLED, polling /api/chart/quote
  // và cập nhật candle hiện tại (không tạo candle duplicate).
  // ---------------------------------------------------------------------------
  function normalizeBar(quote, resolution) {
    // Chỉ dùng quote để cập nhật bar hiện tại; time tính theo resolution.
    var t = Math.floor(Date.now() / 1000);
    var r = String(resolution || '1D').toUpperCase();
    if (r === '1W') {
      var d = new Date(t * 1000);
      var day = (d.getUTCDay() + 6) % 7; // Thứ 2 = 0
      t -= day * 86400;
      var weekStart = new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate()));
      weekStart.setUTCDate(weekStart.getUTCDate() - day);
      t = Math.floor(weekStart.getTime() / 1000);
    } else if (r === '1M') {
      var dm = new Date(t * 1000);
      t = Math.floor(Date.UTC(dm.getUTCFullYear(), dm.getUTCMonth(), 1) / 1000);
    }
    return {
      time: t,
      open: Number(quote.open) || Number(quote.price) || 0,
      high: Math.max(Number(quote.high) || 0, Number(quote.low) || 0, Number(quote.price) || 0),
      low: Math.min(Number(quote.low) || 0, Number(quote.high) || 0, Number(quote.price) || 0),
      close: Number(quote.price) || Number(quote.close) || 0,
      volume: Number(quote.volume) || 0,
    };
  }

  VinaFintechDatafeed.prototype.subscribeBars = function (symbolInfo, resolution, onRealtimeCallback, subscriberUID, onResetCacheNeededCallback) {
    var self = this;
    var symbol = (symbolInfo && (symbolInfo.ticker || symbolInfo.name)) || '';
    this._realtimeSubs[subscriberUID] = { symbol: symbol, resolution: resolution, callback: onRealtimeCallback };

    if (!this._realtimeEnabled) return; // chế độ historical: không đẩy dữ liệu giả

    // Khi có nguồn realtime thật: poll /api/chart/quote và gọi onRealtimeCallback
    // với bar cập nhật. Cơ chế này KHÔNG tạo bar trùng (chỉ cập nhật bar hiện tại).
    var update = function () {
      fetchJson('/quote', { symbol: symbol }).then(function (q) {
        if (!self._realtimeSubs[subscriberUID]) return;
        if (!q || q.valid === false) return;
        if (typeof onRealtimeCallback === 'function') {
          onRealtimeCallback(normalizeBar(q, resolution));
        }
      }).catch(function () {});
    };
    this._realtimeTimer[subscriberUID] = setInterval(update, REALTIME_POLL_MS);
  };

  VinaFintechDatafeed.prototype.unsubscribeBars = function (subscriberUID) {
    delete this._realtimeSubs[subscriberUID];
    if (this._realtimeTimer[subscriberUID]) {
      clearInterval(this._realtimeTimer[subscriberUID]);
      delete this._realtimeTimer[subscriberUID];
    }
  };

  // ---------------------------------------------------------------------------
  // getServerTime: đồng bộ thời gian server (giờ Việt Nam qua backend)
  // ---------------------------------------------------------------------------
  VinaFintechDatafeed.prototype.getServerTime = function (callback) {
    fetchJson('/time', {}).then(function (data) {
      if (typeof callback === 'function') callback(data && data.serverTime ? data.serverTime : Math.floor(Date.now() / 1000));
    }).catch(function () {
      if (typeof callback === 'function') callback(Math.floor(Date.now() / 1000));
    });
  };

  // Cho phép widget tự do đổi mã (compare/đổi symbol) — search từ VinaFintech
  VinaFintechDatafeed.prototype.supportsSymbolSearch = true;

  global.VinaFintechDatafeed = VinaFintechDatafeed;
  global.TradingViewDatafeed = VinaFintechDatafeed; // alias tương thích
})(window);
