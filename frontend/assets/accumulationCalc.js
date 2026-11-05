/* VinaFintech — Thuật toán "Giả định tích sản" DÙNG CHUNG (1 NGUỒN DUY NHẤT).
 *
 * Dùng bởi 2 nơi:
 *   1) frontend/StockDetail.html  → block "Giả định tích sản <MÃ> 5 năm" (trang chi tiết cổ phiếu)
 *   2) frontend/assets/chart/ui/accumulationPanel.js → panel "Giả định tích sản" trong màn hình
 *      "Biểu đồ phân tích kỹ thuật" FULLSCREEN (Bottom Dashboard của chart engine)
 *
 * ⚠️ SỬA THUẬT TOÁN Ở ĐÚNG FILE NÀY — cả 2 màn hình tự động đổi theo (tránh lệch kết quả).
 * ⚠️ File nạp như <script> CỔ ĐIỂN và gắn vào window.TuniXAccum, vì StockDetail.html dùng script
 *    inline cổ điển còn panel fullscreen là ES module (module đọc được biến global).
 *
 * Mô hình: mỗi tháng góp `monthly` VND → 3 đường:
 *   - capital: vốn góp thuần (không lãi)
 *   - bank   : gửi ngân hàng lãi kép BANK_RATE/năm
 *   - stock  : DCA mua cổ phiếu theo giá đóng cửa cuối mỗi tháng + cổ tức (tiền mặt & cổ phiếu thưởng)
 * Đầu vào: history = [{ time, close }] · dividends = [{ date, content }]
 */
(function () {
    'use strict';

    var MONTHS = 60;        // 5 năm = 60 tháng
    var BANK_RATE = 0.07;   // Lãi suất ngân hàng giả định 7%/năm

    function monthKey(date) {
        if (date == null) return '';
        let d;
        if (typeof date === 'string' && /^\d{2}\/\d{2}\/\d{4}$/.test(date.trim())) {
            // Dạng dd/mm/yyyy (ngày trả cổ tức)
            const parts = date.trim().split('/').map(Number);
            d = new Date(parts[2], parts[1] - 1, parts[0]);
        } else {
            d = new Date(date);
        }
        if (Number.isNaN(d.getTime())) return '';
        return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`;
    }

    function monthKeyFrom(y, m0) {
        return `${y}-${String(m0 + 1).padStart(2, '0')}`;
    }

    // Chuỗi số từ nguồn dữ liệu: dấu PHẨY phân cách nghìn + dấu CHẤM là phần thập phân
    // ("5,035.18 đồng/CP" ⇒ 5035.18, KHÔNG phải 503518). Dữ liệu kiểu Việt Nam
    // ("5.035,18") cũng phải đúng ⇒ quy tắc: dấu xuất hiện SAU CÙNG là dấu thập phân.
    // Chỉ có 1 loại dấu mà khối cuối đúng 3 chữ số ⇒ phân cách nghìn ("1,000" ⇒ 1000);
    // còn lại là thập phân ("10.5" ⇒ 10.5).
    function parseVnNumber(s) {
        if (s == null) return NaN;
        let t = String(s).trim().replace(/\s/g, '');
        if (!t) return NaN;
        const iComma = t.lastIndexOf(',');
        const iDot = t.lastIndexOf('.');
        let dec = '';
        if (iComma >= 0 && iDot >= 0) {
            dec = iComma > iDot ? ',' : '.';
        } else {
            const sep = iComma >= 0 ? ',' : (iDot >= 0 ? '.' : '');
            if (sep && t.length - t.lastIndexOf(sep) - 1 !== 3) dec = sep;
        }
        if (dec) {
            const other = dec === ',' ? '.' : ',';
            const i = t.lastIndexOf(dec);
            t = t.slice(0, i).split(other).join('').split(dec).join('') + '.' + t.slice(i + 1);
        } else {
            t = t.split(',').join('').split('.').join('');
        }
        const n = Number(t);
        return Number.isFinite(n) ? n : NaN;
    }

    // Từ khoá chỉ các đợt KHÔNG phải cổ tức/thưởng cho cổ đông → KHÔNG tính vào lợi nhuận:
    // quyền mua, bán ưu đãi, phát hành riêng (ESOP/CBCNV), trái phiếu chuyển đổi, tăng vốn…
    const DIV_EXCLUDE_RE = /(quyền mua|mua cổ phiếu|bán ưu đãi|ưu đãi|chào bán|phát hành riêng|esop|cbc nv|cán bộ công nhân|chuyển đổi|trái phiếu|tăng vốn|đấu giá)/;

    // Phân tích MỘT phần của đợt (một đợt có thể gồm nhiều phần ngăn bởi <br>)
    function parseDividendPart(partText) {
        const text = ' ' + String(partText || '').toLowerCase().replace(/\s+/g, ' ').trim() + ' ';
        const out = { cash: 0, stockPct: 0 };

        // CHỈ nhận đợt cổ tức / thưởng cho cổ đông
        if (!/(cổ tức|thưởng)/.test(text)) return out;
        // Loại quyền mua / bán ưu đãi / phát hành riêng / ESOP / trái phiếu chuyển đổi…
        if (DIV_EXCLUDE_RE.test(text)) return out;
        // Có GIÁ phát hành ("giá 20.000 đ/cp") ⇒ đợt mua ưu đãi (phải bỏ tiền ra mua), không phải cổ tức
        if (/giá\s*[\d.,]+\s*(?:đ|vnd|vnđ|đồng|dong)/.test(text)) return out;

        const isStock = /(cổ phiếu|co phieu|thưởng phiếu|thuong phieu|thưởng cp|thuong cp|cp thưởng)/.test(text);
        const isCash = /(tiền mặt|tien mat|bằng tiền|bang tien)/.test(text) || /[đd]\/\s*(cp|cổ phiếu|co phieu)/.test(text) || /đồng\/(cp|cổ phiếu|co phieu)/.test(text);

        // 1) Tiền mặt theo mệnh giá: "500đ/cp", "1,000 Đồng/CP"
        let m = text.match(/(\d[\d.,]*)\s*(?:đ|vnd|vnđ|đồng|dong)\s*\/\s*(?:cp|cổ phiếu|co phieu)/);
        if (m) {
            out.cash = parseVnNumber(m[1]);
        }

        // 2) Tiền mặt theo % mệnh giá (10.000đ): "bằng tiền mặt 5%", "Cổ tức bằng Tiền, tỷ lệ 10%"
        if (!out.cash && isCash) {
            m = text.match(/(\d+(?:[.,]\d+)?)\s*%/);
            if (m) {
                out.cash = (parseFloat(m[1].replace(',', '.')) / 100) * 10000;
            }
        }

        // 3) Cổ phiếu thưởng theo %: "bằng cổ phiếu 10%", "thưởng phiếu 10%"
        if (isStock) {
            m = text.match(/(\d+(?:[.,]\d+)?)\s*%\s*(?:bằng cổ phiếu|cổ phiếu thưởng|cổ tức bằng cổ phiếu|thưởng phiếu)/) ||
                text.match(/(?:bằng cổ phiếu|cổ phiếu thưởng|cổ tức bằng cổ phiếu|thưởng phiếu)[^%]*?(\d+(?:[.,]\d+)?)\s*%/);
            if (m) {
                out.stockPct = parseFloat(m[m.length - 1].replace(',', '.'));
            }
        }

        // 4) Tỷ lệ chia cổ phiếu "A:B" (vd "20:3" => 15%, "100:15" => 15%, "1:1" => 100%,
        //    "100:10.5" => 10,5%) — chấp nhận cả dấu chấm/phẩy thập phân
        if (isStock || /tỷ lệ\s*\d+\s*[:/]\s*\d+/.test(text)) {
            m = text.match(/tỷ lệ\s*(\d+(?:[.,]\d+)?)\s*[:/]\s*(\d+(?:[.,]\d+)?)/) ||
                text.match(/(\d+(?:[.,]\d+)?)\s*cp\s*[:/]\s*(\d+(?:[.,]\d+)?)\s*cp/);
            if (m) {
                const a = parseFloat(m[1].replace(',', '.'));
                const b = parseFloat(m[2].replace(',', '.'));
                if (a > 0) out.stockPct = (b / a) * 100;
            }
        }

        if (!Number.isFinite(out.cash) || out.cash < 0) out.cash = 0;
        if (!Number.isFinite(out.stockPct) || out.stockPct < 0 || out.stockPct > 1000) out.stockPct = 0;
        return out;
    }

    // Một đợt có thể gồm nhiều phần (vừa cổ tức cổ phiếu vừa cổ tức tiền mặt) →
    // bỏ phần không phải cổ tức/thưởng rồi gộp: tiền mặt lấy phần lớn nhất,
    // tỷ lệ cổ phiếu NHÂN DỒN (tránh cộng trùng khi cùng đợt được mô tả 2 lần).
    function parseDividend(content) {
        const parts = String(content || '').split(/<br\s*\/?>|\n|;|\+/i);
        let cash = 0;
        let stockFactor = 1;
        parts.forEach(part => {
            const r = parseDividendPart(part);
            if (r.cash > cash) cash = r.cash;
            if (r.stockPct > 0) stockFactor *= (1 + r.stockPct / 100);
        });
        const stockPct = stockFactor > 1 ? (stockFactor - 1) * 100 : 0;
        return { cash: cash, stockPct: stockPct };
    }

    function buildMonthlyPrices(history) {
        // Gom giá đóng cửa cuối mỗi tháng từ chuỗi lịch sử hằng ngày/tuần
        const byMonth = {};
        (Array.isArray(history) ? history : []).forEach(bar => {
            const k = monthKey(bar.time);
            const c = Number(bar.close);
            if (!k || !Number.isFinite(c) || c <= 0) return;
            byMonth[k] = c;
        });

        const keys = Object.keys(byMonth).sort();
        if (keys.length === 0) return [];

        // Điểm bắt đầu: mốc 5 năm trước nếu có đủ dữ liệu;
        // ngược lại (cổ phiếu mới IPO) bắt đầu từ tháng có dữ liệu sớm nhất.
        const now = new Date();
        const fullStart = new Date(now.getFullYear(), now.getMonth() - (MONTHS - 1), 1);
        const fullStartKey = monthKey(fullStart);
        const earliest = keys[0];
        const startKey = earliest <= fullStartKey ? fullStartKey : earliest;

        // Tạo timeline từ startKey đến tháng hiện tại
        const [sy, sm] = startKey.split('-').map(Number);
        const months = [];
        const curY = now.getFullYear();
        const curM = now.getMonth() + 1;
        let y = sy;
        let m = sm;
        while (y < curY || (y === curY && m <= curM)) {
            months.push({ year: y, month: m, price: null });
            m += 1;
            if (m > 12) { m = 1; y += 1; }
        }

        // Điền giá: tháng thiếu dữ liệu thì lấy giá tháng gần nhất phía trước (forward fill);
        // ở đầu chuỗi chưa có giá nào thì dùng giá sớm nhất (mốc IPO).
        const firstPrice = byMonth[earliest];
        let lastPrice = null;
        for (let idx = 0; idx < months.length; idx++) {
            const k = monthKeyFrom(months[idx].year, months[idx].month - 1);
            const price = byMonth[k];
            if (price !== undefined && price !== null) {
                months[idx].price = price;
                lastPrice = price;
            } else {
                months[idx].price = lastPrice !== null ? lastPrice : (firstPrice !== undefined ? firstPrice : null);
            }
        }
        return months;
    }

    function computeSeries(months, dividends, monthly) {
        const capital = [];
        const bank = [];
        const stock = [];
        const contributions = [];   // lịch sử góp vốn từng tháng (hiển thị nửa phải)
        const dividendEvents = [];  // lịch sử chia cổ tức đã áp vào danh mục

        // Đường 1: vốn góp thuần (không lãi). Đường 2: gửi ngân hàng lãi kép 7%/năm
        const monthlyRate = BANK_RATE / 12;
        let bankBalance = 0;
        for (let i = 0; i < months.length; i++) {
            capital.push(monthly * (i + 1));
            bankBalance = (bankBalance + monthly) * (1 + monthlyRate);
            bank.push(bankBalance);
        }

        // Gom cổ tức (5 năm) theo tháng — CHỈ những đợt cổ tức/thưởng (xem parseDividend):
        // quyền mua / bán ưu đãi / phát hành riêng… đã bị loại ở bước parse.
        const divByMonth = {};
        const seenDiv = {};   // chống trùng: nguồn dữ liệu đôi khi trả 2 dòng cho CÙNG một đợt
        (Array.isArray(dividends) ? dividends : []).forEach(d => {
            const k = monthKey(d.date);
            if (!k) return;
            const parsed = parseDividend(d.content);
            if (parsed.cash <= 0 && parsed.stockPct <= 0) return;
            // Cùng ngày + cùng giá trị ⇒ coi là cùng một đợt, chỉ tính 1 lần
            const dedupKey = d.date + '|' + parsed.cash.toFixed(2) + '|' + parsed.stockPct.toFixed(4);
            if (seenDiv[dedupKey]) return;
            seenDiv[dedupKey] = true;
            if (!divByMonth[k]) divByMonth[k] = [];
            divByMonth[k].push({ cash: parsed.cash, stockPct: parsed.stockPct, date: d.date, content: d.content });
        });

        // Đường 3: DCA mua cổ phiếu theo giá thị trường + cổ tức
        const hasPrice = months.some(m => m.price !== null && m.price > 0);
        let shares = 0;
        let cash = 0;
        for (let i = 0; i < months.length; i++) {
            const m = months[i];
            const price = m.price;
            let boughtShares = 0;
            if (price !== null && price > 0) {
                boughtShares = monthly / price;
                shares += boughtShares;
            }
            const k = monthKeyFrom(m.year, m.month - 1);
            const divs = divByMonth[k] || [];
            let cashThisMonth = 0;
            let sharesFromDividend = 0;
            divs.forEach(div => {
                const sharesBefore = shares;
                let cashReceived = 0;
                let stockShares = 0;
                if (div.cash > 0) {
                    cashReceived = div.cash * shares;
                    cash += cashReceived;
                    cashThisMonth += cashReceived;
                }
                if (div.stockPct > 0) {
                    stockShares = shares * (div.stockPct / 100);
                    shares += stockShares;
                    sharesFromDividend += stockShares;
                }
                dividendEvents.push({
                    date: div.date,
                    content: div.content,
                    cashPerShare: div.cash,
                    stockPct: div.stockPct,
                    sharesBefore: sharesBefore,
                    cashReceived: cashReceived,
                    stockShares: stockShares,
                    cashTotalAfter: cash,
                    sharesAfter: shares
                });
            });
            contributions.push({
                year: m.year,
                month: m.month,
                price: price,
                boughtShares: boughtShares,
                shares: shares,
                sharesFromDividend: sharesFromDividend,
                cashThisMonth: cashThisMonth,
                cashTotal: cash,
                invested: monthly * (i + 1)
            });
            stock.push(hasPrice && price !== null && price > 0 ? shares * price + cash : capital[i]);
        }

        return { capital, bank, stock, contributions, dividendEvents };
    }

    window.TuniXAccum = {
        MONTHS: MONTHS,
        BANK_RATE: BANK_RATE,
        monthKey: monthKey,
        monthKeyFrom: monthKeyFrom,
        parseVnNumber: parseVnNumber,
        parseDividendPart: parseDividendPart,
        parseDividend: parseDividend,
        buildMonthlyPrices: buildMonthlyPrices,
        computeSeries: computeSeries
    };
})();
