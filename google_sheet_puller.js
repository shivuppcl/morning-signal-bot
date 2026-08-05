/**
 * NSE F&O Data Sheet  —  pulls the 5-minute volume + OI archive into tabs.
 *
 * SETUP (one time, ~3 minutes):
 *  1. Create a new Google Sheet (sheets.new). Name it e.g. "NSE F&O Data".
 *  2. Extensions -> Apps Script. Delete the sample code, paste THIS FILE. Save.
 *  3. Run the function `refreshNow` once (approve permissions when asked).
 *  4. Triggers (clock icon) -> Add Trigger:
 *       Function: refreshNow | Time-driven | Minutes timer | Every 5 minutes
 *
 * TABS CREATED
 *   VOL RAW    cumulative volume per stock at each 5-min snapshot
 *   VOL RATIO  same as x-multiple of YESTERDAY'S FULL DAY  (Rule #1 = 2.00)
 *   OI RAW     live futures OI at each snapshot
 *   OI RATIO   same as x-multiple of yesterday's OI
 *   FNO LIST   current F&O universe + lot sizes (refreshed weekly)
 *
 * PAST DAYS: run `loadDay('2026-08-05')` from the editor to pull any archived
 * date into tabs named "VOL RAW 2026-08-05" etc. The full history lives in
 * the GitHub repo permanently — nothing is ever lost.
 */

const RAW = 'https://raw.githubusercontent.com/shivuppcl/morning-signal-bot/master/data/';

function refreshNow() {
  const d = istToday();
  pull('vol_raw_'   + d + '.csv', 'VOL RAW');
  pull('vol_ratio_' + d + '.csv', 'VOL RATIO');
  pull('oi_raw_'    + d + '.csv', 'OI RAW');
  pull('oi_ratio_'  + d + '.csv', 'OI RATIO');
  pull('fno_list.csv',            'FNO LIST');
}

/** Pull one archived date into its own dated tabs. */
function loadDay(dateStr) {
  pull('vol_raw_'   + dateStr + '.csv', 'VOL RAW '   + dateStr);
  pull('vol_ratio_' + dateStr + '.csv', 'VOL RATIO ' + dateStr);
  pull('oi_raw_'    + dateStr + '.csv', 'OI RAW '    + dateStr);
  pull('oi_ratio_'  + dateStr + '.csv', 'OI RATIO '  + dateStr);
}

function pull(file, tabName) {
  const url = RAW + file + '?cb=' + Date.now();      // cache-bust
  const res = UrlFetchApp.fetch(url, { muteHttpExceptions: true });
  if (res.getResponseCode() !== 200) {
    console.log('skip ' + file + ' -> HTTP ' + res.getResponseCode());
    return;
  }
  const rows = Utilities.parseCsv(res.getContentText());
  if (!rows || !rows.length) return;

  const ss = SpreadsheetApp.getActiveSpreadsheet();
  let sh = ss.getSheetByName(tabName);
  if (!sh) sh = ss.insertSheet(tabName);
  sh.clear();
  sh.getRange(1, 1, rows.length, rows[0].length).setValues(rows);

  // formatting: freeze header + symbol, bold header
  sh.setFrozenRows(1);
  sh.setFrozenColumns(1);
  sh.getRange(1, 1, 1, rows[0].length).setFontWeight('bold');

  // RATIO tabs: green-scale the snapshot columns (Rule#1 = 2.0 threshold)
  if (tabName.indexOf('RATIO') === 0 || tabName.indexOf(' RATIO') > -1) {
    const lastCol = rows[0].length, lastRow = rows.length;
    if (lastCol > 4 && lastRow > 1) {
      const rng = sh.getRange(2, 5, lastRow - 1, lastCol - 4);
      const rule = SpreadsheetApp.newConditionalFormatRule()
        .setGradientMaxpointWithValue('#63BE7B', SpreadsheetApp.InterpolationType.NUMBER, '3')
        .setGradientMidpointWithValue('#FFEB84', SpreadsheetApp.InterpolationType.NUMBER, '2')
        .setGradientMinpointWithValue('#FFFFFF', SpreadsheetApp.InterpolationType.NUMBER, '1')
        .setRanges([rng]).build();
      sh.setConditionalFormatRules([rule]);
    }
  }
  console.log(tabName + ': ' + rows.length + ' rows x ' + rows[0].length + ' cols');
}

function istToday() {
  const now = new Date();
  const ist = new Date(now.getTime() + now.getTimezoneOffset() * 60000 + 5.5 * 3600000);
  const p = n => (n < 10 ? '0' : '') + n;
  return ist.getFullYear() + '-' + p(ist.getMonth() + 1) + '-' + p(ist.getDate());
}
