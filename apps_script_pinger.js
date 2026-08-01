/**
 * GitHub Workflow Pinger — reliable replacement for GitHub's flaky cron.
 * Runs in Google Apps Script on a 5-minute time trigger.
 *
 * What it does (IST, Mon-Fri only):
 *   9:08-9:16   -> triggers the Morning 9:16 signal workflow
 *   8:30-16:30  -> triggers the /scan inbox checker every 5 minutes
 *
 * SETUP (one time, ~7 minutes):
 *  1. Create the GitHub token:
 *     github.com -> your avatar -> Settings -> Developer settings ->
 *     Personal access tokens -> Fine-grained tokens -> Generate new token
 *       Name    : apps-script-pinger
 *       Expiry  : 1 year (custom)
 *       Repository access: Only select repositories -> morning-signal-bot
 *       Permissions -> Repository permissions -> Actions: Read and write
 *     Generate, COPY the token (starts with github_pat_).
 *  2. Go to script.google.com -> New project.
 *     Delete the empty code, paste THIS ENTIRE FILE.
 *     Replace PASTE_TOKEN_HERE below with your token. Save (Ctrl+S).
 *  3. Left sidebar -> Triggers (clock icon) -> Add Trigger:
 *       Function: tick | Event source: Time-driven |
 *       Type: Minutes timer | Interval: Every 5 minutes -> Save.
 *     (Approve the permission popup — it only asks for "external service"
 *      access, which is the GitHub API call.)
 *  4. Test: select function "testMorning" in the toolbar, press Run.
 *     Telegram should get the morning message within ~2 minutes.
 */

const GH_TOKEN = 'PASTE_TOKEN_HERE';
const REPO     = 'shivuppcl/morning-signal-bot';
const BRANCH   = 'master';

function tick() {
  const ist = istNow();
  const day = ist.getDay();                    // 0=Sun ... 6=Sat
  if (day === 0 || day === 6) return;          // weekends off
  const hm = ist.getHours() * 60 + ist.getMinutes();

  // Morning signal: one tick lands in this window; workflow-side
  // concurrency + dedup guarantee a single message.
  if (hm >= 9 * 60 + 8 && hm <= 9 * 60 + 16) dispatch('morning.yml');

  // Evening deals digest: one dispatch window 18:42-18:50 IST
  if (hm >= 18 * 60 + 42 && hm <= 18 * 60 + 50) dispatch('deals.yml');

  // /scan checker: market hours
  if (hm >= 8 * 60 + 30 && hm <= 16 * 60 + 30) dispatch('ondemand.yml');
}

function dispatch(workflowFile) {
  const url = 'https://api.github.com/repos/' + REPO +
              '/actions/workflows/' + workflowFile + '/dispatches';
  const res = UrlFetchApp.fetch(url, {
    method: 'post',
    contentType: 'application/json',
    headers: {
      'Authorization': 'Bearer ' + GH_TOKEN,
      'Accept': 'application/vnd.github+json',
    },
    payload: JSON.stringify({ ref: BRANCH }),
    muteHttpExceptions: true,
  });
  // 204 = accepted. Anything else shows up in Apps Script executions log.
  if (res.getResponseCode() !== 204) {
    console.error(workflowFile + ' -> HTTP ' + res.getResponseCode() +
                  ' ' + res.getContentText().slice(0, 200));
  }
}

function istNow() {
  const now = new Date();
  const utcMs = now.getTime() + now.getTimezoneOffset() * 60000;
  return new Date(utcMs + 5.5 * 3600000);
}

/** Manual test helpers — run from the Apps Script toolbar. */
function testMorning()  { dispatch('morning.yml'); }
function testOndemand() { dispatch('ondemand.yml'); }
