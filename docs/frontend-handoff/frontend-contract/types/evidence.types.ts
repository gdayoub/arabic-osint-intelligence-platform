// Consumer mirror of the JSON returned by src.search.evidence.search_evidence.
// Pin: backend 8d7e241a8073a706900ea81f744a5a879cbe9132. Private offline output, not an HTTP API.
// Offsets are half-open Unicode code-point offsets into original document text.
export interface NameSurface {
  kind: 'original' | 'romanization' | 'verified_alias';
  text: string;
  language: string;
  producer: string | null;
  producer_version: string | null;
  verifier: string | null;
  verification_ref: string | null;
  evidence_mention_id: number | null;
}
export interface EvidenceTranslation {
  document_id: number;
  document_text_sha256: string;
  start: number;
  end: number;
  source_text_sha256: string;
  source_lang: string;
  target_lang: string;
  artifact_ref: string;
  provider: string;
  provider_version: string;
  settings_sha256: string;
  output_text: string;
  record_origin: 'persisted_cache' | 'synthetic_fixture';
  kind: 'cached_machine_translation' | 'synthetic_fixture_translation';
  machine_generated: boolean;
}
export interface EvidenceWindow {
  document_id: number;
  source: string;
  url: string | null;
  content_hash: string;
  document_text_sha256: string;
  document_text_length: number;
  language: string;
  start: number;
  end: number;
  text: string;
  span_sha256: string;
  span_kind: 'sentence' | 'sentence_window' | 'mention_span';
  truncated: boolean;
  sentence_start: number | null;
  sentence_end: number | null;
  segmenter: { name: string; version: string };
  mention: {
    id: number;
    text: string;
    start: number;
    end: number;
    extractor_version_id: number;
    extractor_name: string;
    extractor_version: string;
  };
  translation_status: 'unavailable_for_exact_window' | 'translation_output_exceeds_limit' | 'fixture_only' | 'cached_empty' | 'cached';
  translation: EvidenceTranslation | null;
}
export interface EvidenceEntityResult {
  entity_id: number;
  object_type: string;
  names: NameSurface[];
  matched_names: Array<{ name: NameSurface; match_kind: 'exact' | 'substring' }>;
  evidence: EvidenceWindow[];
  evidence_coverage: {
    active_linked_mentions: number;
    unique_windows: number;
    has_more_windows: boolean;
    mentions_exceeding_span_limit: number;
    status: 'available' | 'no_bounded_live_evidence';
  };
}
export interface OfflineEvidenceResult {
  schema_version: 'offline-evidence-v1';
  reader: { name: 'offline_evidence_search'; version: string };
  data_kind: 'synthetic_fixture' | 'offline_snapshot';
  scope: 'caller_supplied_snapshot';
  entity_id_scope: 'snapshot_local';
  access_scope: 'private_local_only';
  query: string;
  target_language: string;
  limits: { results: number; evidence_per_entity: number; span_characters: number };
  matched_entity_count: number;
  has_more_entities: boolean;
  results: EvidenceEntityResult[];
}
