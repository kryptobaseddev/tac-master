/**
 * CLEO API — reads CLEO tasks.db and exposes epics + tasks as JSON.
 * Endpoints wired in index.ts:
 *   GET /api/cleo/epics               — all epics with child progress
 *   GET /api/cleo/tasks?parent=TXXX   — tasks under an epic
 *   GET /api/cleo/task/:id            — single task detail
 *
 * DB path (first found wins):
 *   1. CLEO_TASKS_DB env var
 *   2. /srv/tac-master/state/cleo-tasks.db  (LXC copy, synced by T035)
 *   3. /mnt/projects/agentic-engineer/.cleo/tasks.db  (dev direct)
 *
 * @task T040
 * @epic T036
 */

import { existsSync } from "node:fs";
import { Database } from "bun:sqlite";

export interface EpicProgress { total:number; done:number; active:number; pending:number; failed:number; }
export interface EpicSummary { id:string; title:string; status:string; priority:string; size:string|null; labels:string[]; progress:EpicProgress; pct:number; }
export interface TaskSummary { id:string; title:string; status:string; type:string|null; priority:string; size:string|null; parent_id:string|null; labels:string[]; acceptance:string[]; }

const DB_CANDIDATES = [process.env.CLEO_TASKS_DB,"/srv/tac-master/state/cleo-tasks.db","/mnt/projects/agentic-engineer/.cleo/tasks.db"].filter(Boolean) as string[];

function resolveDbPath(): string|null { for (const p of DB_CANDIDATES) { if (existsSync(p)) return p; } return null; }

let _db: Database|null=null; let _dbPath: string|null=null;
function getDb(): Database|null {
  const path=resolveDbPath(); if (!path) return null;
  if (_db && _dbPath===path) return _db;
  try { if (_db) { try{_db.close();}catch{} } _db=new Database(path,{readonly:true,create:false}); _dbPath=path; console.log(`[cleo-api] opened ${path}`); }
  catch(e) { console.error("[cleo-api] DB open failed:",e); _db=null; _dbPath=null; }
  return _db;
}

function query<T>(sql:string, params:unknown[]=[]):T[]|null { const db=getDb(); if(!db) return null; try{return db.query(sql).all(...params) as T[];}catch(e){console.error("[cleo-api] query:",e);return null;} }
function queryOne<T>(sql:string, params:unknown[]=[]):T|null { const db=getDb(); if(!db) return null; try{return (db.query(sql).get(...params)??null) as T|null;}catch(e){console.error("[cleo-api] queryOne:",e);return null;} }
function parseJson<T>(raw:string|null|undefined,fallback:T):T { if(!raw) return fallback; try{return JSON.parse(raw) as T;}catch{return fallback;} }
function rowToTask(r:Record<string,unknown>):TaskSummary { return { id:String(r.id), title:String(r.title??""), status:String(r.status??"pending"), type:r.type?String(r.type):null, priority:String(r.priority??"medium"), size:r.size?String(r.size):null, parent_id:r.parent_id?String(r.parent_id):null, labels:parseJson<string[]>(r.labels_json as string|null,[]), acceptance:parseJson<string[]>(r.acceptance_json as string|null,[]) }; }

const DONE=new Set(["done","completed","succeeded"]);
const ACTIVE=new Set(["active","in_progress","running","in-progress"]);
const FAILED=new Set(["failed","blocked","cancelled","canceled"]);

function buildProgress(children:TaskSummary[]):EpicProgress {
  const p:EpicProgress={total:children.length,done:0,active:0,pending:0,failed:0};
  for (const t of children) { const s=t.status.toLowerCase(); if(DONE.has(s))p.done++; else if(ACTIVE.has(s))p.active++; else if(FAILED.has(s))p.failed++; else p.pending++; }
  return p;
}

export function getEpics():{epics:EpicSummary[];dbPath:string|null;error?:string} {
  const dbPath=resolveDbPath();
  const rows=query<Record<string,unknown>>(`SELECT id,title,status,priority,size,labels_json FROM tasks WHERE type='epic' AND (archived_at IS NULL OR archived_at='') ORDER BY CASE priority WHEN 'critical' THEN 0 WHEN 'high' THEN 1 WHEN 'medium' THEN 2 WHEN 'low' THEN 3 ELSE 4 END, created_at ASC`);
  if (rows===null) return {epics:[],dbPath,error:dbPath?"Failed to query tasks.db":"tasks.db not found — set CLEO_TASKS_DB or copy to /srv/tac-master/state/cleo-tasks.db"};
  const epics:EpicSummary[]=rows.map(r=>{const children=getTasksByParent(String(r.id));const progress=buildProgress(children);const pct=progress.total>0?Math.round((progress.done/progress.total)*100):0;return{id:String(r.id),title:String(r.title??""),status:String(r.status??"pending"),priority:String(r.priority??"medium"),size:r.size?String(r.size):null,labels:parseJson<string[]>(r.labels_json as string|null,[]),progress,pct};});
  return {epics,dbPath};
}

export function getTasksByParent(parentId:string):TaskSummary[] {
  const rows=query<Record<string,unknown>>(`SELECT id,title,status,type,priority,size,parent_id,labels_json,acceptance_json FROM tasks WHERE parent_id=? AND (archived_at IS NULL OR archived_at='') ORDER BY position ASC, created_at ASC`,[parentId]);
  if (!rows) return [];
  return rows.map(rowToTask);
}

export function getTaskById(id:string):TaskSummary|null {
  const row=queryOne<Record<string,unknown>>(`SELECT id,title,status,type,priority,size,parent_id,labels_json,acceptance_json FROM tasks WHERE id=?`,[id]);
  return row?rowToTask(row):null;
}
