/**
 * TuniXTradingViewChart — Component bọc TradingView Advanced Charts (charting_library).
 *
 * Nhiệm vụ:
 *   - Kiểm tra & nạp thư viện charting_library (đặt tại /static/tradingview/charting_library/).
 *   - Tạo widget TradingView với datafeed TuniX (TuniXDatafeed).
 *   - Đồng bộ theme (dark/light), đổi symbol, đổi timeframe, visible range.
 *   - Lưu/khôi phục layout chart (saveChartLayout/loadChartLayout — abstraction,
 *     hiện lưu localStorage; sau này đổi sang lưu database TuniX khi có user account).
 *
 * CÁCH CÀI THƯ VIỆN:
 *   Đặt thư mục charting_library (bản cấp phép từ TradingView) vào:
 *   frontend/assets/tradingview/charting_library/
 *   (cần charting_library.min.js + thư mục static/ bên trong, đúng cấu trúc gốc)
 *   Sau đó chart chạy ngay, không cần sửa code.
 */
(function (global) {
  'use strict';

  var LIBRARY_PATH = '/static/tradingview/charting_library/';
  var LIBRARY_SCRIPT = LIBRARY_PATH + 'charting_library.min.js';
  var LAYOUT_STORAGE_PREFIX = 'tunix_tv_layout_'; // key = prefix + SYMBOL

  // ---------------------------------------------------------------------------
  // Storage adapter cho layout chart (saveChartLayout / loadChartLayout)
  // Abstraction để sau này chuyển sang lưu database TuniX:
  //   - backend: GET/PUT /api/chart/layout?symbol=... (cần auth)
  //   - thay đổi 2 hàm dưới là xong, không đụng phần widget.
  // ---------------------------------------------------------------------------
  var layoutStorage = {
    save: function (symbol, json) {
      try {
        if (json) {
          localStorage.setItem(LAYOUT_STORAGE_PREFIX + String(symbol || '').toUpperCase(), json);
        } else {
          localStorage.removeItem(LAYOUT_STORAGE_PREFIX + String(symbol || '').toUpperCase());
        }
      } catch (e) {
        // localStorage có thể bị chặn (private mode) — bỏ qua, không gây crash
      }
    },
    load: function (symbol) {
      try {
        return localStorage.getItem(LAYOUT_STORAGE_PREFIX + String(symbol || '').toUpperCase()) || null;
      } catch (e) {
        return null;
      }
    },
    clear: function (symbol) {
      try {
        localStorage.removeItem(LAYOUT_STORAGE_PREFIX + String(symbol || '').toUpperCase());
      } catch (e) {}
    },
  };

  // ---------------------------------------------------------------------------
  // Kiểm tra thư viện đã có hay chưa (không tải từ nguồn không chính thức)
  // ---------------------------------------------------------------------------
  function probeLibrary() {
    // Ưu tiên kiểm tra qua backend (không tạo lỗi 404 ở console)
    return fetch('/api/chart/library-status', { cache: 'no-store' })
      .then(function (res) { return res.ok ? res.json() : { available: false }; })
      .then(function (data) { return !!(data && data.available); })
      .catch(function () {
        // Fallback: kiểm tra trực tiếp file thư viện
        return fetch(LIBRARY_SCRIPT, { method: 'HEAD', cache: 'no-store' })
          .then(function (res) { return res.ok; })
          .catch(function () { return false; });
      });
  }

  function loadLibraryScript() {
    return new Promise(function (resolve, reject) {
      if (global.TradingView && typeof global.TradingView.widget === 'function') {
        resolve();
        return;
      }
      var script = document.createElement('script');
      script.type = 'text/javascript';
      script.src = LIBRARY_SCRIPT;
      script.async = true;
      script.onload = function () {
        if (global.TradingView && typeof global.TradingView.widget === 'function') {
          resolve();
        } else {
          reject(new Error('charting_library tải xong nhưng TradingView.widget không khả dụng'));
        }
      };
      script.onerror = function () {
        reject(new Error('Không tải được TradingView charting_library (kiểm tra /static/tradingview/charting_library/)'));
      };
      document.head.appendChild(script);
    });
  }

  // ---------------------------------------------------------------------------
  // Component chính
  // ---------------------------------------------------------------------------
  function TuniXTradingViewChart(container, options) {
    options = options || {};
    this._container = typeof container === 'string' ? document.getElementById(container) : container;
    this._options = options;
    this._symbol = String(options.symbol || 'VCB').toUpperCase();
    this._interval = options.interval || '1D';
    this._theme = options.theme === 'dark' ? 'dark' : 'light';
    this._datafeed = options.datafeed || (global.TuniXDatafeed ? new global.TuniXDatafeed() : null);
    this._widget = null;
    this._ready = false;
    this._destroyed = false;
    this._autoSaveHandler = null;

    this.onReady = options.onReady || null;   // callback khi chart sẵn sàng
    this.onError = options.onError || null;   // callback khi lỗi
  }

  // Đồng bộ theme với website TuniX
  TuniXTradingViewChart.prototype.setTheme = function (mode) {
    var theme = mode === 'dark' ? 'dark' : 'light';
    this._theme = theme;
    if (this._widget && typeof this._widget.changeTheme === 'function') {
      try {
        this._widget.changeTheme(theme);
      } catch (e) {
        // Library cũ không hỗ trợ changeTheme -> recreate (hiếm)
      }
    }
  };

  TuniXTradingViewChart.prototype.getTheme = function () {
    return this._theme;
  };

  // Đổi symbol (TuniX gọi khi người dùng đổi mã trên giao diện)
  TuniXTradingViewChart.prototype.setSymbol = function (symbol, interval, callback) {
    var self = this;
    var normalized = String(symbol || '').toUpperCase();
    if (!normalized) return;
    this._symbol = normalized;
    if (interval) this._interval = String(interval).toUpperCase();

    if (this._widget && typeof this._widget.setSymbol === 'function') {
      try {
        this._widget.setSymbol(this._symbol, this._interval, function () {
          if (typeof callback === 'function') callback();
        });
      } catch (e) {
        if (typeof callback === 'function') callback(e);
      }
    } else if (typeof callback === 'function') {
      callback(new Error('Widget chưa sẵn sàng'));
    }
  };

  // Đổi timeframe (resolution)
  TuniXTradingViewChart.prototype.setResolution = function (resolution, callback) {
    this._interval = String(resolution || '1D').toUpperCase();
    if (this._widget && typeof this._widget.setResolution === 'function') {
      try {
        this._widget.setResolution(this._interval, function () {
          if (typeof callback === 'function') callback();
        });
      } catch (e) {
        if (typeof callback === 'function') callback(e);
      }
    } else if (typeof callback === 'function') {
      callback(new Error('Widget chưa sẵn sàng'));
    }
  };

  // Đặt khoảng thời gian hiển thị (dùng cho các nút kỳ hạn 1W/1M/3M/... của TuniX)
  TuniXTradingViewChart.prototype.setVisibleRange = function (from, to) {
    if (this._widget && typeof this._widget.chart === 'function') {
      try {
        var chart = this._widget.chart();
        if (chart && typeof chart.setVisibleRange === 'function') {
          var range = {};
          if (from) range.from = Math.floor(from);
          if (to) range.to = Math.floor(to);
          chart.setVisibleRange(range);
        }
      } catch (e) {}
    }
  };

  // Hiển thị toàn bộ dữ liệu đã tải (dùng cho kỳ hạn ALL)
  TuniXTradingViewChart.prototype.fitContent = function () {
    if (this._widget && typeof this._widget.chart === 'function') {
      try {
        var chart = this._widget.chart();
        if (chart && chart.timeScale && typeof chart.timeScale.fitContent === 'function') {
          chart.timeScale().fitContent();
        }
      } catch (e) {}
    }
  };

  TuniXTradingViewChart.prototype.getInterval = function () {
    return this._interval;
  };

  TuniXTradingViewChart.prototype.getWidget = function () {
    return this._widget;
  };

  TuniXTradingViewChart.prototype.isReady = function () {
    return this._ready && !!this._widget;
  };

  // ---------------------------------------------------------------------------
  // Lưu / khôi phục layout chart (indicator, settings, drawing, màu, timeframe...)
  // Hiện lưu localStorage theo mã cổ phiếu. Để lưu database TuniX sau này,
  // thay nội dung layoutStorage.save/load bằng API (xem chú thích ở trên).
  // ---------------------------------------------------------------------------
  TuniXTradingViewChart.prototype.saveChartLayout = function (callback) {
    var self = this;
    if (!this._widget || typeof this._widget.save !== 'function') {
      if (typeof callback === 'function') callback(null);
      return;
    }
    try {
      this._widget.save(function (data) {
        layoutStorage.save(self._symbol, data || null);
        if (typeof callback === 'function') callback(data || null);
      });
    } catch (e) {
      if (typeof callback === 'function') callback(null);
    }
  };

  TuniXTradingViewChart.prototype.loadChartLayout = function () {
    return layoutStorage.load(this._symbol);
  };

  TuniXTradingViewChart.prototype.clearChartLayout = function () {
    layoutStorage.clear(this._symbol);
  };

  // ---------------------------------------------------------------------------
  // Khởi tạo
  // ---------------------------------------------------------------------------
  TuniXTradingViewChart.prototype.init = function () {
    var self = this;
    var container = this._container;
    if (!container) {
      this._fail('Không tìm thấy vùng chứa biểu đồ');
      return;
    }

    if (!this._datafeed) {
      this._fail('TuniXDatafeed chưa được nạp (thiếu /static/tradingview/tv-datafeed.js)');
      return;
    }

    probeLibrary().then(function (exists) {
      if (self._destroyed) return;
      if (!exists) {
        self._fail('LIBRARY_MISSING');
        return;
      }
      loadLibraryScript().then(function () {
        if (self._destroyed) return;
        self._createWidget();
      }).catch(function (error) {
        self._fail(error && error.message ? error.message : 'LIBRARY_LOAD_ERROR');
      });
    }).catch(function () {
      self._fail('LIBRARY_MISSING');
    });
  };

  TuniXTradingViewChart.prototype._fail = function (message) {
    if (this._destroyed) return;
    var code = message;
    if (typeof this.onError === 'function') {
      this.onError({ code: code, message: this._friendlyMessage(code) });
    }
  };

  TuniXTradingViewChart.prototype._friendlyMessage = function (code) {
    if (code === 'LIBRARY_MISSING') {
      return 'Thư viện TradingView Advanced Charts chưa được cài đặt.';
    }
    return String(code || '');
  };

  TuniXTradingViewChart.prototype._createWidget = function () {
    var self = this;
    var container = this._container;
    if (!container) return;

    container.innerHTML = '';

    var savedLayout = this.loadChartLayout();
    var isDark = this._theme === 'dark';

    var widgetOptions = {
      symbol: this._symbol,
      interval: this._interval,
      container_id: container.id || undefined,
      datafeed: this._datafeed,
      library_path: LIBRARY_PATH,
      locale: 'vi_VN',
      timezone: 'Asia/Ho_Chi_Minh',
      theme: isDark ? 'dark' : 'light',
      autosize: true,
      fullscreen: false,
      debug: false,
      auto_save_delay: 5,
      load_last_chart: false,
      custom_css_url: null,
      toolbar_bg: isDark ? '#161b22' : '#ffffff',
      enabled_features: [
        'side_toolbar_in_fullscreen_mode',
        'header_in_fullscreen_mode',
        'show_interval_dialog_on_key_press',
        'study_templates',
        'create_volume_indicator_by_default',
      ],
      disabled_features: [
        // TuniX dùng auto-save + saveChartLayout/loadChartLayout (localStorage,
        // sẵn sàng nâng cấp lên database TuniX) thay cho dialog Save/Load của TV.
        'header_saveload',
        'volume_force_overlay',
      ],
      overrides: {
        'paneProperties.background': isDark ? '#0d1117' : '#ffffff',
        'paneProperties.vertGridProperties.color': isDark ? 'rgba(255,255,255,0.06)' : 'rgba(46,46,46,0.06)',
        'paneProperties.horzGridProperties.color': isDark ? 'rgba(255,255,255,0.06)' : 'rgba(46,46,46,0.06)',
        'scalesProperties.textColor': isDark ? '#8b949e' : '#6b7a8f',
        'scalesProperties.lineColor': isDark ? '#30363d' : '#e2e8f0',
        'mainSeriesProperties.candleStyle.upColor': '#26a69a',
        'mainSeriesProperties.candleStyle.downColor': '#ef5350',
        'mainSeriesProperties.candleStyle.borderUpColor': '#26a69a',
        'mainSeriesProperties.candleStyle.borderDownColor': '#ef5350',
        'mainSeriesProperties.candleStyle.wickUpColor': '#26a69a',
        'mainSeriesProperties.candleStyle.wickDownColor': '#ef5350',
      },
      studies_overrides: {
        'volume.volume.color.0': '#26a69a',
        'volume.volume.color.1': '#ef5350',
      },
    };

    if (savedLayout) {
      try {
        widgetOptions.saved_data = JSON.parse(savedLayout);
      } catch (e) {
        widgetOptions.saved_data = null;
      }
    }

    try {
      var widget = new global.TradingView.widget(widgetOptions);
      this._widget = widget;
    } catch (e) {
      this._fail('Không thể khởi tạo TradingView chart: ' + (e && e.message ? e.message : ''));
      return;
    }

    // Auto-save layout mỗi khi TradingView báo có thay đổi cần lưu
    if (widget.subscribe) {
      try {
        this._autoSaveHandler = function () {
          self.saveChartLayout();
        };
        widget.subscribe('onAutoSaveNeeded', this._autoSaveHandler);
      } catch (e) {}
    }

    if (widget.onChartReady) {
      widget.onChartReady(function () {
        self._ready = true;
        if (typeof self.onReady === 'function') self.onReady(self);
      });
    }
  };

  // ---------------------------------------------------------------------------
  // Hủy chart (khi chuyển nguồn hiển thị / đổi trang)
  // ---------------------------------------------------------------------------
  TuniXTradingViewChart.prototype.destroy = function () {
    this._destroyed = true;
    this._ready = false;

    if (this._widget) {
      try {
        if (this._autoSaveHandler && this._widget.unsubscribe) {
          this._widget.unsubscribe('onAutoSaveNeeded', this._autoSaveHandler);
        }
        this.saveChartLayout();
      } catch (e) {}
      try {
        if (typeof this._widget.remove === 'function') {
          this._widget.remove();
        }
      } catch (e) {}
      this._widget = null;
    }

    if (this._container) {
      try {
        this._container.innerHTML = '';
      } catch (e) {}
    }
  };

  // API cấp cao
  global.TuniXTradingViewChart = TuniXTradingViewChart;
  global.TuniXChartLayoutStorage = layoutStorage;
})(window);
