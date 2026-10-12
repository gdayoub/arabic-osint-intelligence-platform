// Consumer mirror of src/contracts/atlas.py at backend 8d7e241a8073a706900ea81f744a5a879cbe9132.
// These declarations do not validate JSON. schemas/atlas.schema.json is authoritative.
export interface AtlasArticle {
  id: string;
  title: string;
  title_en: string | null;
  source: string;
  url: string | null;
  topic: string;
  escalation: string;
  country: string | null;
  published_date: string | null;
  collected_at: string | null;
  entity_ids: string[];
}
export interface AtlasEntity {
  id: string;
  name: string;
  name_en: string | null;
  object_type: string;
  surface_forms: string[];
  romanizations: string[];
}
export interface AtlasSnapshot {
  atlas_version: 1;
  generated_at: string;
  snapshot_id: `sha256:${string}`;
  complete: true;
  scope: 'current_processed_live_documents';
  entity_id_scope: 'snapshot_local';
  translation_scope: 'cached_machine_translation';
  classification: { name: string; version: string };
  articles: AtlasArticle[];
  entities: AtlasEntity[];
}
