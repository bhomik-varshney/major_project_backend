"""Typed projections and append-only approval/action/tracking evidence."""
from datetime import timezone
from sqlalchemy import select
from app.db.models import (ApplicationWorkspace,ApplicationFieldRecord,ApplicationDocumentRecord,
    ExternalApplicationSession,UserApproval,MCPAction,ApplicationNote,InterviewEvent,FollowUpTask,now)

def iso(value):
    return (value if value.tzinfo else value.replace(tzinfo=timezone.utc)).isoformat() if value else None

def workspace(db,record):
    data=db.get(ApplicationWorkspace,record.id)
    if not data:
        data=ApplicationWorkspace(application_id=record.id,milestones={},context={},recovery={});db.add(data);db.flush()
    return data

def milestone(db,record,key,**extra):
    data=workspace(db,record);data.milestones={**data.milestones,key:iso(now()),**extra}

def projection(db,record):
    existing={r.field_id:r for r in db.scalars(select(ApplicationFieldRecord).where(ApplicationFieldRecord.application_id==record.id))}
    current={f['id'] for f in record.draft.get('fields',[])}
    for key,row in existing.items():
        if key not in current:db.delete(row)
    for field in record.draft.get('fields',[]):
        row=existing.get(field['id'])
        if row:row.data=field
        else:db.add(ApplicationFieldRecord(application_id=record.id,field_id=field['id'],data=field))
    documents={r.document_ref:r for r in db.scalars(select(ApplicationDocumentRecord).where(ApplicationDocumentRecord.application_id==record.id))}
    selected={d['document_id']:d for d in record.draft.get('documents',[])}
    for key,row in documents.items():
        if key not in selected:row.data={**row.data,'selected':False}
    for key,value in selected.items():
        data={**value,'selected':True}
        if key in documents:documents[key].data=data
        else:db.add(ApplicationDocumentRecord(application_id=record.id,document_id=key,document_ref=key,data=data))

def approve(db,record,kind):
    db.add(UserApproval(application_id=record.id,kind=kind,revision=record.revision,digest=record.approval.get('digest',''),
        scope={'provider':record.provider,'field_ids':[f['id'] for f in record.draft.get('fields',[]) if f.get('approved')],
               'document_ids':[d['document_id'] for d in record.draft.get('documents',[])]}))

def revoke(db,record):
    for approval in db.scalars(select(UserApproval).where(UserApproval.application_id==record.id,UserApproval.revoked.is_(False),UserApproval.kind!='cover_letter')):approval.revoked=True

def tracking_snapshot(db,record):
    tasks=db.scalars(select(FollowUpTask).where(FollowUpTask.application_id==record.id).order_by(FollowUpTask.due_at)).all()
    interviews=db.scalars(select(InterviewEvent).where(InterviewEvent.application_id==record.id).order_by(InterviewEvent.scheduled_at)).all()
    notes=db.scalars(select(ApplicationNote).where(ApplicationNote.application_id==record.id).order_by(ApplicationNote.created_at)).all()
    return {'notes':[{'text':n.text,'created_at':iso(n.created_at)} for n in notes],'tasks':[{'id':t.id,'kind':t.kind,'text':t.text,'due_at':iso(t.due_at),'completed':t.completed} for t in tasks],
        'interviews':[{'id':i.id,'stage':i.stage,'scheduled_at':iso(i.scheduled_at)} for i in interviews]}

def audit(db,record):
    session=db.get(ExternalApplicationSession,record.id)
    return {'candidate_id':record.user_id,'job_id':record.job_id,'source':record.job_snapshot.get('source'),
        'profile_fields_used':sorted({f['source'] for f in record.draft.get('fields',[]) if f.get('source')}),
        'job_fields_used':['id','title','company','location','required_skills','preferred_skills','education_requirements','responsibilities'],
        'mappings':[r.data for r in db.scalars(select(ApplicationFieldRecord).where(ApplicationFieldRecord.application_id==record.id))] or record.draft.get('fields',[]),
        'documents':[r.data for r in db.scalars(select(ApplicationDocumentRecord).where(ApplicationDocumentRecord.application_id==record.id))],
        'approvals':[{'kind':a.kind,'revision':a.revision,'scope':a.scope,'revoked':a.revoked,'created_at':iso(a.created_at)} for a in db.scalars(select(UserApproval).where(UserApproval.application_id==record.id).order_by(UserApproval.created_at))],
        'actions':[{'operation_id':a.operation_id,'tool':a.tool,'status':a.status,'receipt':a.receipt,'error_code':a.error_code,'created_at':iso(a.created_at),'completed_at':iso(a.completed_at)} for a in db.scalars(select(MCPAction).where(MCPAction.application_id==record.id).order_by(MCPAction.created_at))],
        'external_session':{'provider':session.provider,'status':session.status,'external_url':session.external_url,'form_id':session.form_id,'form_version':session.form_version,'opened_at':iso(session.opened_at)} if session else None,
        'credentials_stored':False,'tracking':tracking_snapshot(db,record)}
