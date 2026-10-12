// NEW frontend-local interfaces to implement. Not producer fields or proposed HTTP endpoints.
// Use these as type-only imports / JSDoc in the existing static console's .mjs modules.
import type { DashboardSnapshot } from './dashboard.types';
import type { AtlasSnapshot } from './atlas.types';
import type { OfflineEvidenceResult } from './evidence.types';

export type SourceMode = 'live_public' | 'synthetic_preview';
export type EvidenceExample = 'alias' | 'arabic' | 'romanization' | 'cropped' | 'empty' | 'unbounded' | 'capped' | 'translation';
export interface ConsoleDataAdapter {
  readonly mode: SourceMode;
  loadDashboard(signal: AbortSignal): Promise<DashboardSnapshot>;
  // Absent when public archive activation is off. A missing method is not an error.
  loadArchive?: (signal: AbortSignal) => Promise<AtlasSnapshot>;
  // Only the local preview adapter implements this. No live evidence transport exists.
  readEvidenceExample?: (example: EvidenceExample) => Promise<OfflineEvidenceResult>;
}
export interface ExplorerFilters {
  dataset: 'recent' | 'archive';
  query: string;
  countries: string[];
  includeUnassignedCountry: boolean;
  sources: string[];
  topics: string[];
  includeUnclassifiedTopic: boolean;
  escalationLabels: string[];
  includeUnclassifiedEscalation: boolean;
  entityIds: string[]; // archive only; snapshot-local, never a person permalink
  dateBasis: 'published' | 'collected';
  dateMode: 'all' | 'dated_range' | 'undated_only';
  dateFrom: string | null; // YYYY-MM-DD, inclusive UTC date
  dateTo: string | null; // YYYY-MM-DD, inclusive UTC date
  sort: 'date_desc' | 'date_asc';
  showEnglish: boolean;
}
