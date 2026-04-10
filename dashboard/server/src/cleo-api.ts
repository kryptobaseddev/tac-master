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
import { logOperatorAction } from "./db";

export interface EpicProgress { total:number; done:number; active:number; pending:number; failed:number; }
export interface EpicSummary { id:string; title:string; status:string; priority:string; size:string|null; labels:string[]; progress:EpicProgress; pct:number; }
export interface TaskSummary {
  id:string; title:string; status:string; type:string|null; priority:string;
  size:string|null; parent_id:string|null; labels:string[]; acceptance:string[];
  children_count?:number; children_done?:number;
  depends?:string[];
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
let _dbWrite: Database|null=null; let _dbWritePath: string|null=null;

function getDb(): Database|null {
  const path=resolveDbPath(); if (!path) return null;
  if (_db && _dbPath===path) return _db;
  try { if (_db) { try{_db.close();}catch{} } _db=new Database(path,{readonly:true,create:false}); _dbPath=path; console.log(`[cleo-api] opened ${path}`); }
  catch(e) { console.error("[cleo-api] DB open failed:",e); _db=null; _dbPath=null; }
  return _db;
}

function getDbWrite(): Database|null {
  const path=resolveDbPath(); if (!path) return null;
  if (_dbWrite && _dbWritePath===path) return _dbWrite;
  // Open without readonly flag — default is read-write.
  // TODO: T035 will add bidirectional sync. Writes currently go to LXC copy only.
  try { if (_dbWrite) { try{_dbWrite.close();}catch{} } _dbWrite=new Database(path); _dbWritePath=path; console.log(`[cleo-api] opened write handle ${path}`); }
  catch(e) { console.error("[cleo-api] DB write open failed:",e); _dbWrite=null; _dbWritePath=null; }
  return _dbWrite;
}

function query<T>(sql:string, params:unknown[]=[]):T[]|null { const db=getDb(); if(!db) return null; try{return db.query(sql).all(...params) as T[];}catch(e){console.error("[cleo-api] query:",e);return null;} }
function queryOne<T>(sql:string, params:unknown[]=[]):T|null { const db=getDb(); if(!db) return null; try{return (db.query(sql).get(...params)??null) as T|null;}catch(e){console.error("[cleo-api] queryOne:",e);return null;} }
function execWrite(sql:string, params:unknown[]=[]): boolean { const db=getDbWrite(); if(!db) return false; try{db.query(sql).run(...params);return true;}catch(e){console.error("[cleo-api] execWrite:",e);return false;} }
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

export function getTasksByParent(parentId:string, includeDepends=false):TaskSummary[] {
  const rows=query<Record<string,unknown>>(`SELECT id,title,status,type,priority,size,parent_id,labels_json,acceptance_json${includeDepends?",blocked_by":""} FROM tasks WHERE parent_id=? AND (archived_at IS NULL OR archived_at='') ORDER BY position ASC, created_at ASC`,[parentId]);
  if (!rows) return [];
  return rows.map(r=>{
    const task=rowToTask(r);
    // Add child counts for progress bars on sub-tasks that have children
    const childRows=query<{id:string;status:string}>(`SELECT id,status FROM tasks WHERE parent_id=? AND (archived_at IS NULL OR archived_at='')`, [task.id]);
    if(childRows && childRows.length>0) {
      task.children_count=childRows.length;
      task.children_done=childRows.filter(c=>DONE.has((c.status??"").toLowerCase())).length;
    }
    // Include depends (blocked_by) if requested
    if(includeDepends) {
      task.depends=parseJson<string[]>(r.blocked_by as string|null,[]);
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

// ============================================================
// Write helpers
// ============================================================

function nowUtc():string { return new Date().toISOString().replace("T"," ").replace(/\.\d+Z$/," UTC"); }

/**
 * POST /api/cleo/task/:id/note
 * Appends a timestamped note to the notes_json array.
 */
export function addTaskNote(id:string, text:string):{ok:boolean;error?:string} {
  const row=queryOne<{notes_json:string|null}>(`SELECT notes_json FROM tasks WHERE id=?`,[id]);
  if(!row) return {ok:false,error:"task not found"};
  const notes=parseJson<string[]>(row.notes_json,[]);
  notes.push(`${nowUtc()}: ${text}`);
  const ok=execWrite(`UPDATE tasks SET notes_json=?, updated_at=? WHERE id=?`,[JSON.stringify(notes),new Date().toISOString(),id]);
  return ok?{ok:true}:{ok:false,error:"write failed"};
}

const VALID_STATUSES=new Set(["pending","active","done","blocked","cancelled","failed"]);

/**
 * PATCH /api/cleo/task/:id/status
 * Updates task status and sets completed_at when done.
 */
export function updateTaskStatus(id:string, status:string):{ok:boolean;error?:string} {
  if(!VALID_STATUSES.has(status)) return {ok:false,error:`invalid status: ${status}`};
  const row=queryOne<{id:string}>(`SELECT id FROM tasks WHERE id=?`,[id]);
  if(!row) return {ok:false,error:"task not found"};
  const now=new Date().toISOString();
  const completedAt=["done","completed","succeeded"].includes(status)?now:null;
  const ok=execWrite(
    `UPDATE tasks SET status=?, updated_at=?, completed_at=COALESCE(?,completed_at) WHERE id=?`,
    [status,now,completedAt,id]
  );
  return ok?{ok:true}:{ok:false,error:"write failed"};
}

/**
 * POST /api/cleo/task
 * Creates a new task under a parent epic/task.
 */
export interface CreateTaskBody {
  title:string;
  description?:string;
  parent_id?:string;
  priority?:string;
  type?:string;
  size?:string;
  acceptance?:string[];
}

export function createTask(body:CreateTaskBody):{ok:boolean;task?:TaskDetail;error?:string} {
  if(!body.title?.trim()) return {ok:false,error:"title is required"};
  // Generate next ID: T{max+1}
  const maxRow=queryOne<{max_n:number|null}>(`SELECT MAX(CAST(SUBSTR(id,2) AS INT)) AS max_n FROM tasks WHERE id LIKE 'T%'`,[]);
  const nextN=(maxRow?.max_n??0)+1;
  const id=`T${nextN}`;
  const now=new Date().toISOString();
  const priority=body.priority??"medium";
  const type=body.type??"task";
  const size=body.size??null;
  const acceptance=body.acceptance??[];
  const ok=execWrite(
    `INSERT INTO tasks (id,title,description,parent_id,priority,type,size,status,acceptance_json,notes_json,labels_json,files_json,created_at,updated_at) VALUES (?,?,?,?,?,?,?,'pending',?,?,?,?,?,?)`,
    [id,body.title.trim(),body.description??null,body.parent_id??null,priority,type,size,JSON.stringify(acceptance),JSON.stringify([]),JSON.stringify([]),JSON.stringify([]),now,now]
  );
  if(!ok) return {ok:false,error:"insert failed"};
  const task=getTaskById(id);
  return task?{ok:true,task}:{ok:false,error:"created but fetch failed"};
}

/**
 * POST /api/cleo/task/:id/queue
 * Sets task to active, appends a queue note, and creates a GitHub issue with
 * the 'adw' label so the daemon picks it up within ~20 seconds.
 */
export async function queueTask(id:string):{ok:boolean;queued:boolean;dispatched:boolean;issue_url?:string;issue_number?:number;error?:string} {
  const row=queryOne<{notes_json:string|null;description:string|null;title:string|null;priority:string|null;type:string|null;acceptance_json:string|null}>(`SELECT notes_json,description,title,priority,type,acceptance_json FROM tasks WHERE id=?`,[id]);
  if(!row) return {ok:false,queued:false,dispatched:false,error:"task not found"};

  // 1. Update task status to active and append queue note
  const statusOk=execWrite(`UPDATE tasks SET status='active', updated_at=? WHERE id=?`,[new Date().toISOString(),id]);
  if(!statusOk) return {ok:false,queued:false,dispatched:false,error:"write failed"};
  const notes=parseJson<string[]>(row.notes_json,[]);
  notes.push(`${nowUtc()}: Queued for dispatch by operator — creating GitHub issue`);
  execWrite(`UPDATE tasks SET notes_json=? WHERE id=?`,[JSON.stringify(notes),id]);

  // Log operator action
  logOperatorAction({ source:"operator", action:"queue", message:`Queued ${id} for dispatch`, task_id:id });

  // 2. Create GitHub issue to trigger the daemon
  const ghToken=process.env.GH_TOKEN||process.env.GITHUB_PAT||"";
  const title=`[${id}] ${row.title||id}`;
  const acceptance=parseJson<string[]>(row.acceptance_json,[]);
  const acLines=acceptance.length>0?acceptance.map(a=>`- [ ] ${a}`).join("\n"):"";
  const body=[
    `**CLEO Task**: ${id}`,
    `**Priority**: ${row.priority||"medium"}`,
    `**Type**: ${row.type||"task"}`,
    ``,
    row.description||"*(no description)*",
    ``,
    ...(acLines?[`**Acceptance Criteria**:`,acLines]:[]),
  ].join("\n");

  try {
    const proc=Bun.spawn(
      ["gh","issue","create","--repo","kryptobaseddev/tac-master","--title",title,"--label","adw","--body",body],
      { stdout:"pipe", stderr:"pipe", env:{...process.env,GH_TOKEN:ghToken} }
    );
    const [issueStdout,issueStderr]=await Promise.all([new Response(proc.stdout).text(),new Response(proc.stderr).text()]);
    const exitCode=await proc.exited;

    if(exitCode!==0) {
      const errMsg=issueStderr.trim()||"gh issue create failed";
      // Append error note
      const notesAfterFail=parseJson<string[]>(queryOne<{notes_json:string|null}>(`SELECT notes_json FROM tasks WHERE id=?`,[id])?.notes_json,[]);
      notesAfterFail.push(`${nowUtc()}: ERROR — could not create GitHub issue: ${errMsg}`);
      execWrite(`UPDATE tasks SET notes_json=? WHERE id=?`,[JSON.stringify(notesAfterFail),id]);
      logOperatorAction({ source:"system", action:"error", message:`Failed to create GitHub issue for ${id}: ${errMsg}`, task_id:id });
      return {ok:false,queued:true,dispatched:false,error:errMsg};
    }

    const issueUrl=issueStdout.trim();
    const issueNum=parseInt(issueUrl.split("/").pop()||"0",10)||undefined;

    // Append success note with issue URL
    const notesAfterSuccess=parseJson<string[]>(queryOne<{notes_json:string|null}>(`SELECT notes_json FROM tasks WHERE id=?`,[id])?.notes_json,[]);
    notesAfterSuccess.push(`${nowUtc()}: Dispatched as GitHub issue ${issueUrl}`);
    execWrite(`UPDATE tasks SET notes_json=? WHERE id=?`,[JSON.stringify(notesAfterSuccess),id]);

    logOperatorAction({
      source:"system", action:"dispatch",
      message:`Created GitHub issue #${issueNum} for ${id} — daemon will pick up within 20s`,
      task_id:id, issue_number:issueNum,
      metadata:{ issue_url:issueUrl },
    });

    return {ok:true,queued:true,dispatched:true,issue_url:issueUrl,issue_number:issueNum};
  } catch(e:any) {
    const errMsg=String(e?.message??e);
    logOperatorAction({ source:"system", action:"error", message:`Exception creating GitHub issue for ${id}: ${errMsg}`, task_id:id });
    return {ok:false,queued:true,dispatched:false,error:errMsg};
  }
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
