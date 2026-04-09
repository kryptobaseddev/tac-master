/**
 * CLEO API — reads CLEO tasks.db and exposes epics + tasks as JSON.
 * Endpoints wired in index.ts:
 *   GET /api/cleo/epics               — all epics with child progress (recursive)
 *   GET /api/cleo/tasks?parent=TXXX   — tasks under an epic
 *   GET /api/cleo/task/:id            — single task full detail
 *
 * DB path (first found wins):
 *   1. CLEO_TASKS_DB env var
 *   2. /srv/tac-master/state/cleo-tasks.db  (LXC copy, synced by T035)
 *   3. /mnt/projects/agentic-engineer/.cleo/tasks.db  (dev direct)
 *
 * @task T040 T045
 * @epic T036 T042
 */

import { existsSync } from "node:fs";
import { Database } from "bun:sqlite";

export interface EpicProgress { total:number; done:number; active:number; pending:number; failed:number; }
export interface EpicSummary { id:string; title:string; status:string; priority:string; size:string|null; labels:string[]; progress:EpicProgress; pct:number; }
export interface TaskSummary {
  id:string; title:string; status:string; type:string|null; priority:string;
  size:string|null; parent_id:string|null; labels:string[]; acceptance:string[];
  children_count?:number; children_done?:number;
}

export interface TaskDetail extends TaskSummary {
  description: string|null;
  notes: string[];
  files: string[];
  depends: string[];
  children: {id:string;title:string;status:string}[];
  github_url: string|null;
  assignee: string|null;
}

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

function rowToTask(r:Record<string,unknown>):TaskSummary {
  return {
    id:String(r.id),
    title:String(r.title??""),
    status:String(r.status??"pending"),
    type:r.type?String(r.type):null,
    priority:String(r.priority??"medium"),
    size:r.size?String(r.size):null,
    parent_id:r.parent_id?String(r.parent_id):null,
    labels:parseJson<string[]>(r.labels_json as string|null,[]),
    acceptance:parseJson<string[]>(r.acceptance_json as string|null,[]),
  };
}

const DONE=new Set(["done","completed","succeeded"]);
const ACTIVE=new Set(["active","in_progress","running","in-progress"]);
const FAILED=new Set(["failed","blocked","cancelled","canceled"]);

function buildProgress(children:TaskSummary[]):EpicProgress {
  const p:EpicProgress={total:children.length,done:0,active:0,pending:0,failed:0};
  for (const t of children) { const s=t.status.toLowerCase(); if(DONE.has(s))p.done++; else if(ACTIVE.has(s))p.active++; else if(FAILED.has(s))p.failed++; else p.pending++; }
  return p;
}

/**
 * Recursively count all descendant tasks under a parent ID.
 * Returns {total, done} counts including all depths.
 */
function countDescendants(parentId:string):{total:number;done:number;active:number} {
  const db=getDb(); if(!db) return {total:0,done:0,active:0};
  // Use recursive CTE to gather all descendant IDs
  let total=0,done=0,active=0;
  try {
    const rows=db.query(`
      WITH RECURSIVE descendants(id,status) AS (
        SELECT id,status FROM tasks WHERE parent_id=? AND (archived_at IS NULL OR archived_at='')
        UNION ALL
        SELECT t.id,t.status FROM tasks t
        INNER JOIN descendants d ON t.parent_id=d.id
        WHERE (t.archived_at IS NULL OR t.archived_at='')
      )
      SELECT id,status FROM descendants
    `).all(parentId) as {id:string;status:string}[];
    total=rows.length;
    for (const r of rows) {
      const s=(r.status??"").toLowerCase();
      if(DONE.has(s)) done++;
      else if(ACTIVE.has(s)) active++;
    }
  } catch(e) { console.error("[cleo-api] countDescendants:",e); }
  return {total,done,active};
}

/** Extract GitHub issue URL from notes or description text. */
function extractGithubUrl(texts:string[], repoSlug="kryptobaseddev/tac-master"):string|null {
  const combined=texts.join(" ");
  // Direct URL pattern
  const directMatch=combined.match(/https:\/\/github\.com\/[\w-]+\/[\w-]+\/issues\/(\d+)/);
  if(directMatch) return directMatch[0];
  // Issue #N or issue_num=N or #N patterns
  const issueMatch=combined.match(/\bissue[_ #]*#?(\d+)\b/i) || combined.match(/#(\d+)\b/);
  if(issueMatch) return `https://github.com/${repoSlug}/issues/${issueMatch[1]}`;
  return null;
}

export function getEpics():{epics:EpicSummary[];dbPath:string|null;error?:string} {
  const dbPath=resolveDbPath();
  const rows=query<Record<string,unknown>>(`SELECT id,title,status,priority,size,labels_json FROM tasks WHERE type='epic' AND (archived_at IS NULL OR archived_at='') ORDER BY CASE priority WHEN 'critical' THEN 0 WHEN 'high' THEN 1 WHEN 'medium' THEN 2 WHEN 'low' THEN 3 ELSE 4 END, created_at ASC`);
  if (rows===null) return {epics:[],dbPath,error:dbPath?"Failed to query tasks.db":"tasks.db not found — set CLEO_TASKS_DB or copy to /srv/tac-master/state/cleo-tasks.db"};
  const epics:EpicSummary[]=rows.map(r=>{
    // Use recursive descendant count so nested sub-tasks are included
    const {total,done,active}=countDescendants(String(r.id));
    // If no recursive descendants, fall back to direct children
    let progress:EpicProgress;
    if (total===0) {
      const children=getTasksByParent(String(r.id));
      progress=buildProgress(children);
    } else {
      const children=getTasksByParent(String(r.id));
      const directProgress=buildProgress(children);
      progress={total,done,active,pending:total-done-active-directProgress.failed,failed:directProgress.failed};
    }
    const pct=progress.total>0?Math.round((progress.done/progress.total)*100):0;
    return{id:String(r.id),title:String(r.title??""),status:String(r.status??"pending"),priority:String(r.priority??"medium"),size:r.size?String(r.size):null,labels:parseJson<string[]>(r.labels_json as string|null,[]),progress,pct};
  });
  return {epics,dbPath};
}

export function getTasksByParent(parentId:string):TaskSummary[] {
  const rows=query<Record<string,unknown>>(`SELECT id,title,status,type,priority,size,parent_id,labels_json,acceptance_json FROM tasks WHERE parent_id=? AND (archived_at IS NULL OR archived_at='') ORDER BY position ASC, created_at ASC`,[parentId]);
  if (!rows) return [];
  return rows.map(r=>{
    const task=rowToTask(r);
    // Add child counts for progress bars on sub-tasks that have children
    const childRows=query<{id:string;status:string}>(`SELECT id,status FROM tasks WHERE parent_id=? AND (archived_at IS NULL OR archived_at='')`, [task.id]);
    if(childRows && childRows.length>0) {
      task.children_count=childRows.length;
      task.children_done=childRows.filter(c=>DONE.has((c.status??"").toLowerCase())).length;
    }
    return task;
  });
}

export interface CleoStats { total:number; done:number; active:number; pending:number; blocked:number; error?:string; }

export function getCleoStats():CleoStats {
  const dbPath=resolveDbPath();
  if (!dbPath) return {total:0,done:0,active:0,pending:0,blocked:0,error:"tasks.db not found"};
  // Aggregate across all non-epic, non-archived tasks
  const rows=query<{status:string}>(`SELECT status FROM tasks WHERE (type IS NULL OR type!='epic') AND (archived_at IS NULL OR archived_at='')`);
  if (!rows) return {total:0,done:0,active:0,pending:0,blocked:0,error:"Query failed"};
  const out:CleoStats={total:rows.length,done:0,active:0,pending:0,blocked:0};
  for (const r of rows) {
    const s=(r.status??"").toLowerCase();
    if (DONE.has(s)) out.done++;
    else if (ACTIVE.has(s)) out.active++;
    else if (FAILED.has(s)) out.blocked++;
    else out.pending++;
  }
  return out;
}

export function getTaskById(id:string):TaskDetail|null {
  const row=queryOne<Record<string,unknown>>(
    `SELECT id,title,description,status,type,priority,size,parent_id,labels_json,acceptance_json,notes_json,files_json,blocked_by,assignee FROM tasks WHERE id=?`,
    [id]
  );
  if(!row) return null;
  const base=rowToTask(row);
  const notes=parseJson<string[]>(row.notes_json as string|null,[]);
  const files=parseJson<string[]>(row.files_json as string|null,[]);
  const depends=row.blocked_by?parseJson<string[]>(row.blocked_by as string|null,[]):[];
  // Get direct children
  const childRows=query<Record<string,unknown>>(`SELECT id,title,status FROM tasks WHERE parent_id=? AND (archived_at IS NULL OR archived_at='') ORDER BY position ASC, created_at ASC`,[id]);
  const children=(childRows??[]).map(c=>({id:String(c.id),title:String(c.title??""),status:String(c.status??"pending")}));
  // Extract GitHub URL from description + notes
  const allText=[row.description as string|null,...notes].filter(Boolean) as string[];
  const github_url=extractGithubUrl(allText);
  return {
    ...base,
    description:row.description?String(row.description):null,
    notes:notes.slice(-3).reverse(), // 3 most recent, newest first
    files,
    depends:Array.isArray(depends)?depends:[],
    children,
    github_url,
    assignee:row.assignee?String(row.assignee):null,
  };
}
