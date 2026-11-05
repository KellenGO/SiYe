import axios from "axios";

/** 空间接口一律 15 秒超时：以前没有任何超时，后端卡住时弹窗既等不到结果、
 *  界面也没有反馈，只能刷新页面。 */
const TIMEOUT = 15000;
import { publicResult } from "./bookmarks.js";
import { resultSources } from "./resultTools.js";
import type { NoteDocument } from "./spaceNotes.js";
import type { UnifiedSearchResult } from "../types/search.js";

export interface ResearchSpace {
  id: number;
  name: string;
  description: string;
  archived: boolean;
  item_count: number;
  created_at: string;
  updated_at: string;
}

export interface SpaceDetail extends ResearchSpace {
  items: { key: string; result: UnifiedSearchResult; added_at: string }[];
  note_document: NoteDocument;
  note_format_version: number;
  note_revision: number;
}

export interface SpacesState {
  spaces: ResearchSpace[];
  active_space_id: number | null;
}

export function spaceSources(result: UnifiedSearchResult): UnifiedSearchResult[] {
  return resultSources(result).map(publicResult);
}

const BASE = "/api/spaces";
export async function fetchSpaces(): Promise<SpacesState> { return (await axios.get(BASE, { timeout: TIMEOUT })).data; }
export async function fetchSpace(id: number): Promise<SpaceDetail> { return (await axios.get(`${BASE}/${id}`, { timeout: TIMEOUT })).data; }
export async function createSpace(name: string, description: string): Promise<SpaceDetail> { return (await axios.post(BASE, { name, description }, { timeout: TIMEOUT })).data; }
export async function updateSpace(id: number, name: string, description: string): Promise<SpaceDetail> { return (await axios.patch(`${BASE}/${id}`, { name, description }, { timeout: TIMEOUT })).data; }
export async function activateSpace(id: number | null): Promise<void> { await axios.put(`${BASE}/active`, { active_space_id: id }, { timeout: TIMEOUT }); }
export async function archiveSpace(id: number, archived: boolean): Promise<SpaceDetail> { return (await axios.put(`${BASE}/${id}/archive`, { archived }, { timeout: TIMEOUT })).data; }
export async function deleteSpace(id: number): Promise<void> { await axios.delete(`${BASE}/${id}`, { timeout: TIMEOUT }); }
export async function addSpaceItems(id: number, result: UnifiedSearchResult): Promise<{ added: number }> { return (await axios.post(`${BASE}/${id}/items`, { results: spaceSources(result) }, { timeout: TIMEOUT })).data; }
export async function removeSpaceItem(id: number, result: UnifiedSearchResult): Promise<void> { await axios.delete(`${BASE}/${id}/items`, { data: { keys: spaceSources(result).map((source) => ({ platform: source.platform, content_id: source.content_id })) }, timeout: TIMEOUT }); }
export async function saveSpaceNote(id: number, document: NoteDocument, revision: number): Promise<{ note_revision: number }> {
  return (await axios.put(`${BASE}/${id}/note`, { document, format_version: 1, revision }, { timeout: TIMEOUT })).data;
}
