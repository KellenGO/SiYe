import axios from "axios";
import type { NoteDocument } from "./spaceNotes.js";

export interface ResearchConfig {
  base_url: string; model: string; has_key: boolean; key_mask: string;
  runtime: { sdk_available: boolean; cli_available: boolean; sdk_version: string; cli_version: string };
}
export interface MaterialComponent {
  state: string; count: number;
  reason: string; truncated: boolean; sort?: string;
}
export interface ResearchMaterial {
  key: string; platform: string; title: string; url: string;
  body: MaterialComponent; comments: MaterialComponent; subtitles: MaterialComponent;
}
export interface ResearchJob {
  job_id: string; space_id: number; web_enabled: boolean; question: string;
  conversation_id: string;
  status: "collecting" | "awaiting_sources" | "analyzing" | "ready" | "failed" | "cancelled";
  phase: string; message: string; error: string; materials: ResearchMaterial[];
  total_materials: number;
  document: NoteDocument | null; stale_snapshot?: boolean;
  coverage: { key: string; title: string; chunks: number; read_chunks: number; complete: boolean }[];
  external_sources: { id: string; title: string; url: string; level: string; fetched_at: string }[];
  web_errors: string[]; usage: Record<string, number> | null; cost_usd: number | null;
}

const BASE = "/api/research";
export async function getResearchConfig(): Promise<ResearchConfig> { return (await axios.get(`${BASE}/config`)).data; }
export async function saveResearchConfig(base_url: string, model: string, api_key?: string): Promise<ResearchConfig> {
  return (await axios.put(`${BASE}/config`, { base_url, model, api_key })).data;
}
export async function deleteResearchConfig(): Promise<ResearchConfig> { return (await axios.delete(`${BASE}/config`)).data; }
export async function testResearchConnection(web_enabled: boolean): Promise<{ connection_ok: boolean; web_ok: boolean }> {
  return (await axios.post(`${BASE}/connection-test`, { web_enabled }, { timeout: 100_000 })).data;
}
export async function getWebPreference(spaceId: number): Promise<{ web_enabled: boolean }> { return (await axios.get(`${BASE}/spaces/${spaceId}/preference`)).data; }
export async function saveWebPreference(spaceId: number, web_enabled: boolean): Promise<{ web_enabled: boolean }> {
  return (await axios.put(`${BASE}/spaces/${spaceId}/preference`, { web_enabled })).data;
}
export async function latestResearch(spaceId: number): Promise<ResearchJob | null> { return (await axios.get(`${BASE}/spaces/${spaceId}/latest`)).data; }
export async function createResearch(space_id: number, question: string, web_enabled: boolean, conversation_id?: string): Promise<ResearchJob> {
  return (await axios.post(`${BASE}/jobs`, { space_id, question, web_enabled, conversation_id })).data;
}
export interface ResearchConversation { id: string; title: string; turns: number; status: ResearchJob["status"]; latest_job_id: string; }
export async function listResearchConversations(spaceId: number): Promise<ResearchConversation[]> { return (await axios.get(`${BASE}/spaces/${spaceId}/conversations`)).data; }
export async function getResearchConversation(spaceId: number, id: string): Promise<ResearchJob[]> { return (await axios.get(`${BASE}/spaces/${spaceId}/conversations/${id}`)).data; }
export async function getResearch(id: string): Promise<ResearchJob> { return (await axios.get(`${BASE}/jobs/${id}`)).data; }
export async function researchAction(id: string, action: "retry" | "generate" | "cancel"): Promise<ResearchJob> {
  return (await axios.post(`${BASE}/jobs/${id}/${action}`)).data;
}
