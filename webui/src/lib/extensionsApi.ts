import axios from "axios";
export interface ExtensionStatus { id: string; name: string; installed: boolean; enabled: boolean; version: string | null; compatible: boolean; phase: string; downloaded: number; total: number | null; error: string; available: { version: string; size: number } | null; }
const BASE = "/api/extensions/ai";
export async function extensionStatus(): Promise<ExtensionStatus> { return (await axios.get(BASE)).data; }
export async function extensionCatalog(): Promise<ExtensionStatus> { return (await axios.get(`${BASE}/catalog`)).data; }
export async function installExtension(): Promise<ExtensionStatus> { return (await axios.post(`${BASE}/install`)).data; }
export async function cancelExtensionInstall(): Promise<ExtensionStatus> { return (await axios.post(`${BASE}/cancel`)).data; }
export async function enableExtension(enabled: boolean): Promise<ExtensionStatus> { return (await axios.put(`${BASE}/enabled`, { enabled })).data; }
export async function uninstallExtension(clear_data: boolean): Promise<ExtensionStatus> { return (await axios.delete(BASE, { params: { clear_data } })).data; }
