export type Case = { id: string; name: string; document_count?: number };
export type Document = { id: string; name: string; size: number; pages: number; created_at: string };
export type Passage = { id: string; page: number; text: string };
export type Evidence = Document & { passages: Passage[] };
export type Citation = { passage_id: string; document_id: string; document_name: string; page: number; quote: string; analysis?: string };
export type Agent = { id: string; name: string; instruction: string; keywords: string; icon: string };
export type Task = { id: string; agent_id: string; name: string; status: string; mode: string; created_at: string; findings: Citation[]; error?: string; provider?: string; model?: string };
export type Message = { id: string; role: string; text: string; citations: Citation[]; mode: string };
export type Provider = { id: string; name: string; configured: boolean; external: boolean; host: string; model: string; max_excerpts: number; models?: string[]; key_env?: string };
export type Settings = {
  provider: Provider;
  providers: Provider[];
  supported_extensions: string[];
  max_upload_mb: number;
  deployment: string;
  data_location: string;
};
