"""Versioned complete-task review, alongside and never migrating legacy tables."""
import json
import os
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path
import task_data_store as legacy

STATUSES = ('pending_review', 'approved', 'needs_revision', 'excluded')
SCHEMA = '''
CREATE TABLE IF NOT EXISTS construct_task (
 id TEXT PRIMARY KEY, current_revision INTEGER NOT NULL, source TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS construct_revision (
 task_id TEXT NOT NULL, revision INTEGER NOT NULL, payload TEXT NOT NULL,
 qc TEXT NOT NULL, assets TEXT NOT NULL, observations TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'pending_review', note TEXT NOT NULL DEFAULT '',
 reviewer TEXT, reviewed_at TEXT, confirmations TEXT NOT NULL DEFAULT '[]', created_at TEXT NOT NULL,
 PRIMARY KEY(task_id,revision));
CREATE TABLE IF NOT EXISTS construct_review_history (
 id INTEGER PRIMARY KEY, task_id TEXT NOT NULL, revision INTEGER NOT NULL,
 action TEXT NOT NULL, before_value TEXT NOT NULL, after_value TEXT NOT NULL,
 reviewer TEXT NOT NULL, created_at TEXT NOT NULL);
'''


def connect(path=None):
    db = legacy.connect(path)
    db.executescript(SCHEMA)
    return db


def observe(paths):
    result = {}
    for name in sorted(set(str(p) for p in paths if p)):
        p=Path(name)
        try:
            s=p.stat(); result[name]=dict(size=s.st_size, modified_ns=s.st_mtime_ns)
        except OSError:
            result[name]=None
    return result


def import_task(db, payload, qc, assets, observations, source):
    ident=payload['meta']['review_id']
    old=db.execute('SELECT r.* FROM construct_task t JOIN construct_revision r ON r.task_id=t.id '
                   'AND r.revision=t.current_revision WHERE t.id=?',(ident,)).fetchone()
    content=dict(payload=payload,qc=qc,assets=assets,observations=observations)
    if old and all(json.loads(old[k])==v for k,v in content.items()):
        db.execute('UPDATE construct_task SET active=1,source=? WHERE id=?', (source, ident))
        return old['revision'], False
    revision=old['revision']+1 if old else 1
    db.execute('INSERT INTO construct_revision(task_id,revision,payload,qc,assets,observations,created_at) '
               'VALUES (?,?,?,?,?,?,?)',(ident,revision,*(legacy.dumps(content[k]) for k in content),legacy.now()))
    db.execute('INSERT INTO construct_task VALUES (?,?,?,1) ON CONFLICT(id) DO UPDATE SET current_revision=excluded.current_revision, source=excluded.source, active=1',
               (ident,revision,source))
    if old:
        db.execute('INSERT INTO construct_review_history(task_id,revision,action,before_value,after_value,reviewer,created_at) '
                   'VALUES (?,?,?,?,?,?,?)',(ident,revision,'new_revision',legacy.dumps(dict(revision=old['revision'],status=old['status'])),
                    legacy.dumps(dict(revision=revision,status='pending_review')),'manifest_import',legacy.now()))
    return revision, True


def records(db, include_inactive=False):
    rows=db.execute('SELECT r.* FROM construct_task t JOIN construct_revision r ON r.task_id=t.id '
                    'AND r.revision=t.current_revision ' + ('' if include_inactive else 'WHERE t.active=1 ') + 'ORDER BY t.id')
    return [decode(r) for r in rows]


def decode(row):
    r=dict(row)
    for k in ('payload','qc','assets','observations','confirmations'):r[k]=json.loads(r[k])
    return r


def get(db, ident, revision=None):
    if revision is None:
        row=db.execute('SELECT current_revision FROM construct_task WHERE id=?',(ident,)).fetchone()
        if not row:raise KeyError(ident)
        revision=row[0]
    row=db.execute('SELECT * FROM construct_revision WHERE task_id=? AND revision=?',(ident,revision)).fetchone()
    if not row:raise KeyError(ident)
    result=decode(row)
    result['history']=[dict(r) for r in db.execute('SELECT * FROM construct_review_history WHERE task_id=? ORDER BY id',(ident,))]
    result['revisions']=[dict(r) for r in db.execute('SELECT revision,status,created_at,reviewer,reviewed_at FROM construct_revision WHERE task_id=? ORDER BY revision',(ident,))]
    return result


def decide(db, ident, revision, status, note, reviewer, confirmations=()):
    if status not in STATUSES[1:]:raise ValueError('请选择通过、需修改或剔除')
    if not reviewer.strip():raise ValueError('请填写审核人')
    row=get(db,ident)
    if not db.execute('SELECT active FROM construct_task WHERE id=?', (ident,)).fetchone()[0]:
        raise ValueError('任务已从当前清单移除')
    if row['revision'] != revision:raise ValueError('任务已修订，请刷新后审核最新版本')
    if status=='approved' and observe(row['observations']) != row['observations']:
        raise ValueError('源文件已变化，请重新导入、QC 后审核')
    if status == 'approved' and not set(row['qc'].get('manual_checks', [])).issubset(confirmations):
        raise ValueError('批准前请逐项确认人工核查项')
    stamp=legacy.now()
    before={k:row[k] for k in ('status','note','reviewer','reviewed_at','confirmations')}
    after=dict(status=status,note=note,reviewer=reviewer.strip(),reviewed_at=stamp,confirmations=sorted(set(confirmations)))
    db.execute('INSERT INTO construct_review_history(task_id,revision,action,before_value,after_value,reviewer,created_at) VALUES (?,?,?,?,?,?,?)',
               (ident,revision,'human_review',legacy.dumps(before),legacy.dumps(after),reviewer.strip(),stamp))
    db.execute('UPDATE construct_revision SET status=?,note=?,reviewer=?,reviewed_at=?,confirmations=? WHERE task_id=? AND revision=?',
               (status,note,reviewer.strip(),stamp,legacy.dumps(after['confirmations']),ident,revision))


def export(db):
    accepted=[]; excluded=[]
    for row in records(db):
        reasons=[]
        if row['status']!='approved':reasons.append('not_human_approved')
        if not row['qc']['passed']:reasons.append('qc_not_passed')
        if not set(row['qc'].get('manual_checks', [])).issubset(row['confirmations']):reasons.append('manual_checks_missing')
        if observe(row['observations'])!=row['observations']:reasons.append('source_changed_since_qc')
        if reasons:
            excluded.append(dict(id=row['task_id'],revision=row['revision'],reasons=reasons))
        else:
            value=dict(row['payload'])
            value['review_status']='approved'
            value['meta']=dict(value['meta'],review_status='approved',review_revision=row['revision'],
                               reviewer=row['reviewer'],reviewed_at=row['reviewed_at'],review_note=row['note'],review_confirmations=row['confirmations'],qc_passed=True)
            accepted.append(value)
    return dict(tasks=accepted,excluded=excluded,total=len(accepted),policy='Current revision, explicit human approval and QC pass only')


def archive(db):
    """Include removed tasks and every payload version in the portable audit archive."""
    result = []
    for row in records(db, include_inactive=True):
        item = get(db, row['task_id'])
        item['active'] = bool(db.execute('SELECT active FROM construct_task WHERE id=?', (row['task_id'],)).fetchone()[0])
        item['versions'] = [decode(r) for r in db.execute('SELECT * FROM construct_revision WHERE task_id=? ORDER BY revision', (row['task_id'],))]
        result.append(item)
    return result


def backup(db, destination=None):
    """Back up committed state atomically; call after the importing/review transaction.

    CONSTRUCT_REVIEW_BACKUP is optional. The source connection and old tables are
    unchanged; the destination is a complete SQLite snapshot, including history.
    """
    destination = destination or os.environ.get('CONSTRUCT_REVIEW_BACKUP')
    if not destination:
        return None
    target = Path(destination)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=target.name + '.', suffix='.tmp', dir=target.parent)
    os.close(fd)
    try:
        with closing(sqlite3.connect(temporary)) as snapshot:
            db.backup(snapshot)
        os.replace(temporary, target)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return str(target)
