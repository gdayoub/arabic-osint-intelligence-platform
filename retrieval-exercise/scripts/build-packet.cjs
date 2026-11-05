// Builds the review packet from original texts and hand-authored question specs.
// No network, model, GPU, or third-party dependencies. Existing review files are never overwritten.
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');
const root = path.join(__dirname, '..', 'data', 'labels');
const articles = path.join(__dirname, '..', 'data', 'articles');
const hash = bytes => crypto.createHash('sha256').update(bytes).digest('hex');
const manifestBytes = fs.readFileSync(path.join(articles, 'manifest.json'));
const manifest = JSON.parse(manifestBytes);
const specsBytes = fs.readFileSync(path.join(root, 'packet-spec.json'));
const specs = JSON.parse(specsBytes);
if (manifest.documents.length !== 50 || specs.length !== 40) throw Error('Unexpected sample size');
const documents = new Map();
for (const d of manifest.documents) {
  const bytes = fs.readFileSync(path.join(articles, d.local_text_file));
  const text = new TextDecoder('utf-8', {fatal: true}).decode(bytes);
  if (hash(bytes) !== d.text_hash || Array.from(text).length !== d.text_length) throw Error('Source mismatch: '+d.id);
  documents.set(d.id, {...d, text});
}
const records = specs.map(s => {
  const d = documents.get(s.legacy_document_id);
  if (!d) throw Error('Unknown document');
  const start16 = d.text.indexOf(s.start_anchor);
  const endAnchor16 = d.text.indexOf(s.end_anchor, start16+s.start_anchor.length);
  if (start16 < 0 || endAnchor16 < 0) throw Error('Missing anchor: '+s.question_id);
  const end16 = endAnchor16+s.end_anchor.length;
  const original_span = d.text.slice(start16,end16);
  const start = Array.from(d.text.slice(0,start16)).length;
  const end = Array.from(d.text.slice(0,end16)).length;
  if (Array.from(d.text).slice(start,end).join('') !== original_span) throw Error('Offset mismatch');
  return {
    question_id:s.question_id, query_en:s.query_en, query_language:'en', query_direction:'ltr',
    query_origin:'assistant_authored_from_sample; not independent user search traffic',
    document_id:d.document_id, legacy_document_id:d.id, text_hash:d.text_hash,
    source:d.source, source_ref:d.source_ref, published_at:d.published_at,collected_at:d.collected_at,
    data_snapshot_id:manifest.data_snapshot_id, local_text_file:d.local_text_file,
    passage:{start,end,offset_unit:'unicode_code_point',end_exclusive:true,language:'ar',direction:'rtl',original_span},
    english_gloss:s.english_gloss,gloss_language:'en',gloss_direction:'ltr',gloss_status:s.gloss_status,
    human_label:null,human_reason:'',reviewer:null,arabic_verified:null,
    answerability_in_50:'not_reviewed',
    story_group:null,duplicate_group:d.text_hash,identity_group:null,
    split:'evaluation_candidate_quarantine',group_review_status:'pending',
    eligible_for_training:false,eligible_for_validation_or_test:false,
    retriever_model_version:null,scores:null,abstention:null
  };
});
const header = `# Review 40 questions

George: fill in **Your label** and **Your reason** below each question. Save this file in VS Code.
This Markdown file is the human-editable review worksheet; review-40.json is the initial machine-readable draft.
After you finish, we will import your decisions into a new version; do not regenerate over your edits.

## How to label

Judge whether the displayed passage helps answer the exact English question.

- **relevant**: provides the requested answer, including an explicit statement that something is unknown, undisclosed, or not established when that answers the question.
- **partly_relevant**: provides useful evidence but leaves part of the requested answer unsupported.
- **irrelevant**: provides no useful evidence for the requested answer; shared keywords alone are not enough.
- **unsure**: translation, wording, scope, or evidence is ambiguous. Explain the issue instead of forcing a label.

A negative answer can be relevant. Missing an answer does not automatically mean the passage is unrelated.
Relevance means the article supplies evidence, not that its reported claims have been independently fact-checked.
Glosses are assistant drafts, not independently verified translations; quotations and attribution may be paraphrased.
If a gloss looks wrong, record the correction. Leave Arabic verification blank unless you actually checked it.
**Corpus answerability** is a different question: do not mark a query unanswerable in all 50 documents just because this one passage lacks its answer.
Use not_reviewed unless you checked the collection; if another passage answers it, record that document and span.

## What this packet is

40 unique draft questions, each with one candidate passage from a different document, drawn from the 50-document local sample.
This is an initial annotation pass, not exhaustive relevance judgments for retrieval metrics.
Later we must pool more candidates and check other valid answers before calculating Recall, nDCG, or MRR.
Questions were written after reading the sample; this can make them easier than natural searches.
The 50 documents stay together in evaluation-candidate quarantine. No training pairs are generated.
Story and identity group review, human labels, corpus answerability, and test-set freezing remain pending.

Snapshot: retrieval-fifty-v0.1. Offsets below are zero-based Unicode code points, end exclusive, against original text.

`;
let md=header;
for(const r of records){
 const d=documents.get(r.legacy_document_id);
 md += `---\n\n## ${r.question_id} — ${r.query_en}\n\n**Source:** ${r.source} · document ${r.legacy_document_id} · published ${r.published_at || 'not recorded'}\n\n[Original local text](${r.local_text_file}) · [Source URL](${r.source_ref})\n\n**Arabic passage** — code points [${r.passage.start}, ${r.passage.end}):\n\n<div lang="ar" dir="rtl">\n\n${r.passage.original_span}\n\n</div>\n\n**English gloss (draft):** ${r.english_gloss}\n\n**Your label:** \n\n**Your reason:** \n\n**Arabic verified (yes/no/unsure):** \n\n**Gloss correction, if any:** \n\n**Corpus answerability (not_reviewed/answerable/unanswerable/unsure):** not_reviewed\n\n**Other relevant document/span, if found:** \n\n`;
}
const dataset = {
 schema_version:'0.1',packet_version:'review-40-v0.1',data_snapshot_id:manifest.data_snapshot_id,
 generated_at:new Date().toISOString(),generator_version:'build-packet.cjs-v0.1',
 manifest_sha256:hash(manifestBytes),question_specs_sha256:hash(specsBytes),
 configuration:{normalization:'none',candidate_selection:'assistant curated; one passage per question',labels:'blank',split_policy:'whole sample quarantined from training pending human group review'},
 model_version:null,records
};
for(const name of ['REVIEW-40.md','review-40.json','integrity-report.json']) {
 if(fs.existsSync(path.join(root,name))) throw Error('Refusing to overwrite '+name);
}
fs.writeFileSync(path.join(root,'REVIEW-40.md'),md,{flag:'wx'});
fs.writeFileSync(path.join(root,'review-40.json'),JSON.stringify(dataset,null,2)+'\n',{flag:'wx'});
const report={
 documents:documents.size,unique_text_hashes:new Set(manifest.documents.map(d=>d.text_hash)).size,
 sources:new Set(manifest.documents.map(d=>d.source)).size,
 questions:records.length,unique_questions:new Set(records.map(r=>r.query_en)).size,
 candidate_documents:new Set(records.map(r=>r.document_id)).size,
 all_source_hashes_and_lengths_verified:true,all_original_spans_verified:true,
 all_labels_blank:records.every(r=>r.human_label===null),
 no_training_or_test_eligible:records.every(r=>!r.eligible_for_training&&!r.eligible_for_validation_or_test),
 review_json_sha256:hash(fs.readFileSync(path.join(root,'review-40.json'))),
 review_markdown_initial_sha256:hash(fs.readFileSync(path.join(root,'REVIEW-40.md'))),
 metadata_flags:manifest.documents.filter(d=>d.metadata_flags.length).map(d=>({id:d.id,flags:d.metadata_flags}))
};
fs.writeFileSync(path.join(root,'integrity-report.json'),JSON.stringify(report,null,2)+'\n',{flag:'wx'});
console.log(JSON.stringify(report,null,2));
