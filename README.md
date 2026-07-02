# Morning 9:15 Volume Signal Bot

Scans all 206 NSE F&O stocks right after the 9:15 opening bar closes and sends
Telegram alerts for stocks whose first-bar volume is >= 3x yesterday's first bar
with a price move >= 0.5%. MEGA tag = first bar alone >= 50% of yesterday's
entire day volume.

Runs on GitHub Actions cloud — your computer does NOT need to be on.

## One-time setup (10 minutes)

1. **Create a GitHub account** at github.com (skip if you have one).

2. **Create a new PRIVATE repository** — click "+" (top right) -> New repository
   - Name: `morning-signal-bot`
   - Visibility: **Private**
   - Click Create.

3. **Upload these files** — in the new repo click "uploading an existing file"
   and drag in:
   - `morning_signal_bot.py`
   - `requirements.txt`
   - the `.github` folder (if drag-drop doesn't keep the folder, create the
     file manually: Add file -> Create new file -> type
     `.github/workflows/morning.yml` as the name and paste the yml content).

4. **Add your Telegram secrets** — repo Settings -> Secrets and variables ->
   Actions -> New repository secret:
   - Name `TELEGRAM_TOKEN`  -> value = the token from telegram_config.ini
   - Name `TELEGRAM_CHAT_ID` -> value = the chat_id from telegram_config.ini

5. **Test it** — Actions tab -> "Morning 9-15 Volume Signal" -> Run workflow.
   Within ~3 minutes a Telegram message should arrive.

Done. Every trading morning it wakes at ~9:20 IST, waits for the 9:15 bar to
close, scans, and messages you by ~9:35 IST.

## Notes
- GitHub cron has a few minutes of dispatch jitter — the message lands between
  9:32 and 9:45 IST, never before the bar data is final.
- NSE holidays: the scan still runs but finds no fresh bars and reports
  "no spikes" (data would be from the previous session). Harmless.
- To change thresholds edit `BAR1_HIKE_MIN` / `PRICE_MIN_CHG` at the top of
  `morning_signal_bot.py` and commit.
