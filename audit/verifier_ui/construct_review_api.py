"""Complete versioned review routes mounted by the existing task_data API."""
import json
import sqlite3
from collections import Counter
from contextlib import closing
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response, JSONResponse
from pydantic import BaseModel, Field

import construct_review_store as store

router = APIRouter(prefix='/construct')


class Decision(BaseModel):
    ident: str
    revision: int
    status: Literal['approved', 'needs_revision', 'excluded']
    note: str = ''
    reviewer: str = Field(min_length=1)
    confirmations: list[str] = Field(default_factory=list)


@router.get('/tasks')
def tasks(task_type: str = '', target: str = '', relation: str = '', status: str = '', qc: str = ''):
    with closing(store.connect()) as db:
        records = store.records(db)
    items = []
    for r in records:
        p, m = r['payload'], r['payload']['meta']
        item = dict(id=r['task_id'], revision=r['revision'], task_type=p.get('task_type'),
                    target=m.get('target_object', p.get('target', '')), relation=m.get('relation_type', ''),
                    status=r['status'], qc='pass' if r['qc']['passed'] else 'blocked',
                    data_version=m.get('data_version'), dataset=m.get('dataset'), scene_id=m.get('scene_id'))
        items.append(item)
    selected = [r for r in items if all(not value or r[key] == value for key, value in
                [('task_type', task_type), ('target', target), ('relation', relation), ('status', status), ('qc', qc)])]
    return dict(items=selected, total=len(items), filtered_total=len(selected),
                reviewed=sum(r['status'] != 'pending_review' for r in selected),
                status_counts=dict(Counter(r['status'] for r in items)),
                options={key: sorted({str(r[key]) for r in items}) for key in ('task_type', 'target', 'relation', 'status', 'qc')})


@router.get('/item')
def item(ident: str, revision: int | None = None):
    try:
        with closing(store.connect()) as db:
            result = store.get(db, ident, revision)
        result['sources_current'] = store.observe(result['observations']) == result['observations']
        return result
    except KeyError:
        raise HTTPException(404, '任务版本不存在')


@router.post('/review')
def review(body: Decision):
    try:
        with closing(store.connect()) as db:
            with db:
                store.decide(db, body.ident, body.revision, body.status, body.note, body.reviewer, body.confirmations)
            try:
                destination = store.backup(db)
            except (OSError, sqlite3.Error) as exc:
                return JSONResponse(status_code=503, content=dict(
                    saved_local=True, backup_ok=False,
                    detail=f'审核已保存到本地，但共享备份失败：{exc}。请联系维护者补做备份。'))
        return dict(ok=True, backup=destination)
    except KeyError:
        raise HTTPException(404, '任务不存在')
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@router.get('/image')
def image(ident: str, revision: int, key: Literal['t1', 't2', 'overlay', 'reference']):
    try:
        with closing(store.connect()) as db:
            record = store.get(db, ident, revision)
        return Response(Path(record['assets'][key]).read_bytes(), media_type='image/png')
    except (KeyError, OSError):
        raise HTTPException(404, '预览未生成；请通过离线构建补齐')


@router.get('/export')
def export(kind: Literal['approved', 'pending', 'history'] = 'approved'):
    with closing(store.connect()) as db:
        if kind == 'approved':
            result = store.export(db)
        elif kind == 'pending':
            result = dict(tasks=store.records(db))
        else:
            result = dict(tasks=store.archive(db))
    return Response(json.dumps(result, ensure_ascii=False, indent=2), media_type='application/json',
                    headers={'Content-Disposition': f'attachment; filename="construct_{kind}.json"'})
