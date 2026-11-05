// Local-only review tool. Run with Node; no packages or model dependencies.
const http = require('http');
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');
const root = __dirname;
const labels = path.join(root, '..', 'data', 'labels');
const articles = path.join(root, '..', 'data', 'articles');
const test = process.argv.includes('--test');
const port = test ? 8875 : 8874;
const origin = `http://127.0.0.1:${port}`;
const token = crypto.randomBytes(24).toString('hex');
const packetBytes = fs.readFileSync(path.join(labels, 'review-40.json'));
const packetHash = crypto.createHash('sha256').update(packetBytes).digest('hex');
const packet = JSON.parse(packetBytes);
const records = new Map(packet.records.map(r => [r.question_id, r]));
const journal = path.join(labels, test ? 'ui-test-history.jsonl' : 'button-review-history.jsonl');
const decisions = {};
if (fs.existsSync(journal)) {
  for (const line of fs.readFileSync(journal, 'utf8').split('\n').filter(Boolean)) {
    const event = JSON.parse(line);
    if (event.packet_sha256 !== packetHash) throw Error('Review history belongs to a different packet');
    decisions[event.question_id] = event;
  }
}
function send(res, status, data, type = 'application/json; charset=utf-8') {
  res.writeHead(status, {'Content-Type':type, 'Cache-Control':'no-store', 'X-Content-Type-Options':'nosniff', 'Content-Security-Policy':"default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'"});
  res.end(type.startsWith('application/json') ? JSON.stringify(data) : data);
}
function exportPacket() {
  return {...packet, review_interface_version:'buttons-v1', exported_at:new Date().toISOString(), original_packet_sha256:packetHash,
    records:packet.records.map(r => {
      const d = decisions[r.question_id];
      if (!d) return r;
      return {...r, human_label:d.approved ? d.label : null, human_reason:d.approved ? d.reason : '', reviewer:d.approved ? 'George' : null,
        arabic_verified:d.approved ? d.arabic_verified : null, gloss_correction:d.approved ? d.gloss_correction : '',
        review_status:d.approved ? 'approved' : 'draft', reviewed_at:d.approved ? d.saved_at : null,
        draft_review:d, eligible_for_training:false, eligible_for_validation_or_test:false};
    })};
}
const server = http.createServer(async (req, res) => {
  try {
    if (req.headers.host !== `127.0.0.1:${port}`) return send(res,403,{error:'Local host required'});
    const url = new URL(req.url, origin);
    if (req.method === 'GET' && url.pathname === '/') return send(res,200,fs.readFileSync(path.join(root,'review-ui.html')),'text/html; charset=utf-8');
    const assets = {'/review-ui.js':'text/javascript; charset=utf-8','/review-ui.css':'text/css; charset=utf-8'};
    if (req.method === 'GET' && assets[url.pathname]) return send(res,200,fs.readFileSync(path.join(root,url.pathname.slice(1))),assets[url.pathname]);
    if (req.method === 'GET' && url.pathname === '/api/packet') return send(res,200,{packet,decisions,token,test});
    if (req.method === 'GET' && url.pathname === '/api/export') {
      res.setHeader('Content-Disposition','attachment; filename="george-reviewed-40.json"');
      return send(res,200,exportPacket());
    }
    if (req.method === 'GET' && url.pathname.startsWith('/api/original/')) {
      const r=records.get(url.pathname.slice('/api/original/'.length));
      if (!r) return send(res,404,{error:'Unknown question'});
      return send(res,200,{text:fs.readFileSync(path.join(articles,r.local_text_file),'utf8')});
    }
    if (req.method !== 'POST' || url.pathname !== '/api/review') return send(res,404,{error:'Not found'});
    if (req.headers.origin !== origin || req.headers['x-review-token'] !== token) return send(res,403,{error:'Open this page locally to save reviews'});
    let body='';
    for await (const chunk of req) {body+=chunk; if(body.length>32000) return send(res,413,{error:'Review too large'});}
    const input=JSON.parse(body), r=records.get(input.question_id);
    const labels=[null,'relevant','partly_relevant','irrelevant','unsure'];
    if (!r || !labels.includes(input.label) || ![null,true,false].includes(input.arabic_verified) || typeof input.approved !== 'boolean' || typeof input.reason !== 'string' || typeof input.gloss_correction !== 'string' || input.reason.length>6000 || input.gloss_correction.length>6000 || (input.approved && !input.label)) return send(res,400,{error:'Choose a valid label before approving'});
    const event={event_id:crypto.randomUUID(),packet_sha256:packetHash,question_id:r.question_id,document_id:r.document_id,text_hash:r.text_hash,
      label:input.label,reason:input.reason,arabic_verified:input.arabic_verified,gloss_correction:input.gloss_correction,
      approved:input.approved,reviewer:'George',saved_at:new Date().toISOString(),event_type:input.approved?'human_approval':'draft_save',test};
    // Persist first, then acknowledge. Earlier decisions remain in the journal.
    const fd=fs.openSync(journal,'a');
    try {fs.writeSync(fd,JSON.stringify(event)+'\n');fs.fsyncSync(fd);} finally {fs.closeSync(fd);}
    decisions[r.question_id]=event;
    return send(res,200,{decision:event});
  } catch(err) { console.error(err.message); send(res,500,{error:'Could not save or load. Your source packet is unchanged. Please retry.'}); }
});
server.listen(port,'127.0.0.1',()=>console.log(`${test?'TEST':'REVIEW'} ${origin}`));
