const SHEET_NAME = 'Subscribers';

function doGet(e) {
  if (e && e.parameter && e.parameter.action === 'list') {
    const token = e.parameter.token || '';
    if (!isValidToken_(token)) return json_({ok:false, error:'unauthorized'});
    return json_({ok:true, subscribers:listSubscribers_()});
  }
  return HtmlService.createHtmlOutput(INDEX_HTML)
    .setTitle('IPK Monitor — підписка')
    .setXFrameOptionsMode(HtmlService.XFrameOptionsMode.ALLOWALL);
}

function setup() {
  let id = PropertiesService.getScriptProperties().getProperty('SPREADSHEET_ID');
  let ss;
  if (id) {
    ss = SpreadsheetApp.openById(id);
  } else {
    ss = SpreadsheetApp.create('IPK Monitor — Subscribers');
    id = ss.getId();
    PropertiesService.getScriptProperties().setProperty('SPREADSHEET_ID', id);
  }
  let sheet = ss.getSheetByName(SHEET_NAME);
  if (!sheet) sheet = ss.insertSheet(SHEET_NAME);
  if (sheet.getLastRow() === 0) sheet.appendRow(['email', 'created_at', 'active']);

  let token = PropertiesService.getScriptProperties().getProperty('API_TOKEN');
  if (!token) {
    token = Utilities.getUuid().replace(/-/g, '') + Utilities.getUuid().replace(/-/g, '');
    PropertiesService.getScriptProperties().setProperty('API_TOKEN', token);
  }
  Logger.log('SPREADSHEET_URL=' + ss.getUrl());
  Logger.log('API_TOKEN=' + token);
  return {spreadsheetUrl:ss.getUrl(), apiToken:token};
}

function getSpreadsheet_() {
  let id = PropertiesService.getScriptProperties().getProperty('SPREADSHEET_ID');
  if (!id) setup();
  id = PropertiesService.getScriptProperties().getProperty('SPREADSHEET_ID');
  return SpreadsheetApp.openById(id);
}

function addSubscriber(email) {
  email = String(email || '').trim().toLowerCase();
  if (!isValidEmail_(email)) throw new Error('Введіть коректну електронну адресу.');
  const ss = getSpreadsheet_();
  const sheet = ss.getSheetByName(SHEET_NAME);
  const values = sheet.getDataRange().getValues();
  for (let i = 1; i < values.length; i++) {
    if (String(values[i][0]).trim().toLowerCase() === email) {
      if (String(values[i][2]).toLowerCase() !== 'true') sheet.getRange(i + 1, 3).setValue(true);
      return 'Ця адреса вже була підписана.';
    }
  }
  sheet.appendRow([email, new Date(), true]);
  return 'Готово. Адресу додано до розсилки.';
}

function listSubscribers_() {
  const sheet = getSpreadsheet_().getSheetByName(SHEET_NAME);
  if (!sheet || sheet.getLastRow() < 2) return [];
  const values = sheet.getDataRange().getValues();
  const out = [];
  for (let i = 1; i < values.length; i++) {
    const email = String(values[i][0] || '').trim().toLowerCase();
    const active = values[i][2] === true || String(values[i][2]).toLowerCase() === 'true';
    if (active && isValidEmail_(email)) out.push(email);
  }
  return [...new Set(out)];
}

function isValidToken_(token) {
  const expected = PropertiesService.getScriptProperties().getProperty('API_TOKEN');
  return !!expected && token === expected;
}

function isValidEmail_(email) {
  return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email);
}

function json_(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj)).setMimeType(ContentService.MimeType.JSON);
}

const INDEX_HTML = `
<!doctype html><html lang="uk"><head>
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>IPK Monitor</title>
<style>
body{font-family:Arial,sans-serif;background:#f5f7f5;margin:0;color:#18201a}
.card{max-width:520px;margin:48px auto;background:white;border-radius:20px;padding:28px;box-shadow:0 8px 30px #0001}
h1{margin-top:0}p{line-height:1.5;color:#56605a}
input{width:100%;box-sizing:border-box;padding:15px;border:1px solid #cbd3cd;border-radius:12px;font-size:16px;margin:8px 0 14px}
button{width:100%;padding:15px;border:0;border-radius:12px;background:#16734a;color:white;font-size:16px;font-weight:700}
#msg{margin-top:16px;padding:12px;border-radius:10px;display:none}.ok{background:#e8f7ed;color:#17633d}.err{background:#fdecec;color:#9b2525}.small{font-size:13px}
</style></head><body><div class="card">
<h1>IPK Monitor</h1><p>Підпишіться на повідомлення про нові індивідуальні податкові консультації.</p>
<form id="form"><label for="email">Електронна адреса</label>
<input id="email" type="email" placeholder="name@gmail.com" required autocomplete="email">
<button type="submit">Підписатися</button></form><div id="msg"></div>
<p class="small">Адреса використовується лише для розсилки IPK Monitor.</p>
</div><script>
document.getElementById('form').addEventListener('submit',function(ev){ev.preventDefault();const msg=document.getElementById('msg');msg.className='';msg.style.display='block';msg.textContent='Додаємо адресу…';google.script.run.withSuccessHandler(function(text){msg.className='ok';msg.textContent=text;document.getElementById('email').value='';}).withFailureHandler(function(err){msg.className='err';msg.textContent=err.message||'Помилка. Спробуйте ще раз.';}).addSubscriber(document.getElementById('email').value);});
</script></body></html>`;
