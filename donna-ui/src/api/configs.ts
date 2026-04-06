import client from "./client";

export interface ConfigFile {
  name: string;
  size_bytes: number;
  modified: number;
}

export interface ConfigContent {
  name: string;
  content: string;
  size_bytes: number;
  modified: number;
}

export interface PromptFile {
  name: string;
  size_bytes: number;
  modified: number;
}

export interface PromptContent {
  name: string;
  content: string;
  size_bytes: number;
  modified: number;
}

export async function fetchConfigs(): Promise<ConfigFile[]> {
  const resp = await client.get("/admin/configs");
  return resp.data.configs;
}

export async function fetchConfig(name: string): Promise<ConfigContent> {
  const resp = await client.get(`/admin/configs/${name}`);
  return resp.data;
}

export async function saveConfig(name: string, content: string): Promise<ConfigFile> {
  const resp = await client.put(`/admin/configs/${name}`, { content });
  return resp.data;
}

export async function fetchPrompts(): Promise<PromptFile[]> {
  const resp = await client.get("/admin/prompts");
  return resp.data.prompts;
}

export async function fetchPrompt(name: string): Promise<PromptContent> {
  const resp = await client.get(`/admin/prompts/${name}`);
  return resp.data;
}

export async function savePrompt(name: string, content: string): Promise<PromptFile> {
  const resp = await client.put(`/admin/prompts/${name}`, { content });
  return resp.data;
}
