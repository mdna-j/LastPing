from typing import Dict, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, status, Response, Cookie, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from sqlmodel import Session, select
from datetime import datetime

from ..db import get_session
from ..models import ApiKey, Project, AuditLog
from ..security import generate_api_key, hash_api_key
import os
import secrets
from sqlmodel import Session
from ..models import AdminCsrf
from datetime import timedelta


router = APIRouter(prefix="/admin/apikeys", tags=["admin_apikeys"])


class ApiKeyRead(BaseModel):
    id: int
    project_id: int
    rate_limit_per_minute: Optional[int]

    class Config:
        orm_mode = True


@router.get("/", response_model=Dict[int, list])
def list_apikeys(x_admin_token: Optional[str] = Header(None), session: Session = Depends(get_session)):
    admin_token = os.environ.get('ADMIN_TOKEN')
    if not admin_token or x_admin_token != admin_token:
        raise HTTPException(status_code=403, detail="Admin token required")
    rows = session.exec(select(ApiKey)).all()
    out: Dict[int, list] = {}
    for a in rows:
        out.setdefault(a.project_id, []).append(ApiKeyRead.from_orm(a))
    return out


@router.post("/create", status_code=status.HTTP_201_CREATED)
def create_apikey(project_id: int, rate_limit_per_minute: Optional[int] = 0, x_admin_token: Optional[str] = Header(None), x_csrf_token: Optional[str] = Header(None), admin_csrf: Optional[str] = Cookie(None), session: Session = Depends(get_session)):
    admin_token = os.environ.get('ADMIN_TOKEN')
    if not admin_token or x_admin_token != admin_token:
        raise HTTPException(status_code=403, detail="Admin token required")
    project = session.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    # CSRF protection: require matching token in header and cookie
    if not x_csrf_token or not admin_csrf or x_csrf_token != admin_csrf:
        raise HTTPException(status_code=403, detail="Invalid CSRF token")
    # Optional server-side validation/consume of token
    if os.environ.get('ADMIN_CSRF_SERVER_SIDE', '0') == '1':
        now = datetime.utcnow()
        ac = session.exec(select(AdminCsrf).where(AdminCsrf.token == x_csrf_token, AdminCsrf.expires_at >= now)).first()
        if not ac:
            raise HTTPException(status_code=403, detail='CSRF token invalid or expired')
        # consume single-use token
        try:
            session.delete(ac)
            session.commit()
        except Exception:
            pass
    plain = generate_api_key()
    ak = ApiKey(project_id=project_id, key_hash=hash_api_key(plain), rate_limit_per_minute=rate_limit_per_minute or 0)
    session.add(ak)
    session.commit()
    session.refresh(ak)
    # audit
    al = AuditLog(actor="admin", action="create_apikey", target_type="project", target_id=project_id, details=f"apikey_id={ak.id}")
    session.add(al)
    session.commit()
    return {"api_key": plain, "id": ak.id}


@router.post("/revoke", status_code=status.HTTP_200_OK)
def revoke_apikey(api_key_id: int, x_admin_token: Optional[str] = Header(None), x_csrf_token: Optional[str] = Header(None), admin_csrf: Optional[str] = Cookie(None), session: Session = Depends(get_session)):
    admin_token = os.environ.get('ADMIN_TOKEN')
    if not admin_token or x_admin_token != admin_token:
        raise HTTPException(status_code=403, detail="Admin token required")
    # CSRF double-submit check
    if not x_csrf_token or not admin_csrf or x_csrf_token != admin_csrf:
        raise HTTPException(status_code=403, detail="Invalid CSRF token")
    # server-side validation if enabled
    if os.environ.get('ADMIN_CSRF_SERVER_SIDE', '0') == '1':
        now = datetime.utcnow()
        ac = session.exec(select(AdminCsrf).where(AdminCsrf.token == x_csrf_token, AdminCsrf.expires_at >= now)).first()
        if not ac:
            raise HTTPException(status_code=403, detail='CSRF token invalid or expired')
        try:
            session.delete(ac)
            session.commit()
        except Exception:
            pass

    ak = session.get(ApiKey, api_key_id)
    if not ak:
        raise HTTPException(status_code=404, detail="ApiKey not found")
    pid = ak.project_id
    session.delete(ak)
    # audit
    al = AuditLog(actor="admin", action="revoke_apikey", target_type="project", target_id=pid, details=f"apikey_id={api_key_id}")
    session.add(al)
    session.commit()
    return {"revoked": api_key_id}


@router.get('/audit')
def list_audit(limit: int = 100, x_admin_token: Optional[str] = Header(None), session: Session = Depends(get_session)):
    admin_token = os.environ.get('ADMIN_TOKEN')
    if not admin_token or x_admin_token != admin_token:
        raise HTTPException(status_code=403, detail="Admin token required")
    rows = session.exec(select(AuditLog).order_by(AuditLog.created_at.desc()).limit(limit)).all()
    return rows


@router.get('/audit/search')
def search_audit(
    action: Optional[str] = None,
    actor: Optional[str] = None,
    target_type: Optional[str] = None,
    target_id: Optional[int] = None,
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
    page: int = 1,
    per_page: int = 50,
    x_admin_token: Optional[str] = Header(None),
    session: Session = Depends(get_session),
):
    admin_token = os.environ.get('ADMIN_TOKEN')
    if not admin_token or x_admin_token != admin_token:
        raise HTTPException(status_code=403, detail="Admin token required")

    stmt = select(AuditLog)
    # build filters
    if action:
        stmt = stmt.where(AuditLog.action == action)
    if actor:
        stmt = stmt.where(AuditLog.actor == actor)
    if target_type:
        stmt = stmt.where(AuditLog.target_type == target_type)
    if target_id is not None:
        stmt = stmt.where(AuditLog.target_id == target_id)
    if start:
        stmt = stmt.where(AuditLog.created_at >= start)
    if end:
        stmt = stmt.where(AuditLog.created_at <= end)

    # ordering + pagination
    total = session.exec(stmt).count()
    stmt = stmt.order_by(AuditLog.created_at.desc()).offset((max(page, 1) - 1) * per_page).limit(per_page)
    items = session.exec(stmt).all()
    return {"total": total, "page": page, "per_page": per_page, "items": items}


@router.post('/rotate-project')
def rotate_project_key(project_id: int, x_admin_token: Optional[str] = Header(None), x_csrf_token: Optional[str] = Header(None), admin_csrf: Optional[str] = Cookie(None), session: Session = Depends(get_session)):
    admin_token = os.environ.get('ADMIN_TOKEN')
    if not admin_token or x_admin_token != admin_token:
        raise HTTPException(status_code=403, detail="Admin token required")
    # CSRF protection
    if not x_csrf_token or not admin_csrf or x_csrf_token != admin_csrf:
        raise HTTPException(status_code=403, detail="Invalid CSRF token")
    if os.environ.get('ADMIN_CSRF_SERVER_SIDE', '0') == '1':
        now = datetime.utcnow()
        ac = session.exec(select(AdminCsrf).where(AdminCsrf.token == x_csrf_token, AdminCsrf.expires_at >= now)).first()
        if not ac:
            raise HTTPException(status_code=403, detail='CSRF token invalid or expired')
        try:
            session.delete(ac)
            session.commit()
        except Exception:
            pass
    project = session.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    new = generate_api_key()
    project.api_key_hash = hash_api_key(new)
    session.add(project)
    session.commit()
    # audit
    al = AuditLog(actor="admin", action="rotate_project_key", target_type="project", target_id=project_id, details=None)
    session.add(al)
    session.commit()
    return {"api_key": new}


@router.get('/csrf')
def get_csrf_token(x_admin_token: Optional[str] = Header(None), session: Session = Depends(get_session)):
    """Return a CSRF token for admin UI operations.

    If `ADMIN_CSRF_SERVER_SIDE` is set to `1`, store the token in the DB and
    validate on subsequent requests. Token expiry defaults to 10 minutes.
    """
    admin_token = os.environ.get('ADMIN_TOKEN')
    if not admin_token or x_admin_token != admin_token:
        raise HTTPException(status_code=403, detail="Admin token required")

    token = secrets.token_urlsafe(24)
    secure_flag = True if os.environ.get('ENV') == 'production' or os.environ.get('REQUIRE_HTTPS_FOR_ADMIN', '1') == '1' else False
    httponly_flag = True if os.environ.get('ADMIN_CSRF_HTTPONLY', '1') == '1' else False

    # optional server-side storage
    if os.environ.get('ADMIN_CSRF_SERVER_SIDE', '0') == '1':
        # store token and expiry
        now = datetime.utcnow()
        expires = now + timedelta(minutes=10)
        ac = AdminCsrf(token=token, created_at=now, expires_at=expires)
        session.add(ac)
        session.commit()

    # return token and set cookie for double-submit; JS can also use JSON response
    resp = {"csrf": token}
    response = HTMLResponse(content=str(resp))
    response.set_cookie('admin_csrf', token, httponly=httponly_flag, secure=secure_flag, samesite='Lax')
    return resp


@router.get('/ui', response_model=None)
def admin_apikeys_ui(request: Request, x_admin_token: Optional[str] = Header(None)):
        """Simple admin-only web UI for API key management.

        This returns a small HTML page that uses the admin endpoints. It
        requires the `X-ADMIN-TOKEN` header to be provided by the browser
        (or the user can paste it into the prompt the page shows).
        """
        admin_token = os.environ.get('ADMIN_TOKEN')
        if not admin_token or x_admin_token != admin_token:
            raise HTTPException(status_code=403, detail="Admin token required")

        # enforce HTTPS in production / when requested by env var
        require_https = os.environ.get('REQUIRE_HTTPS_FOR_ADMIN', '1')
        if require_https == '1':
            proto = request.headers.get('x-forwarded-proto') or request.url.scheme
            if proto != 'https':
                raise HTTPException(status_code=403, detail='Admin UI requires HTTPS (use reverse-proxy with TLS)')

        # create a CSRF token and set it as a cookie (double-submit)
        token = secrets.token_urlsafe(16)
        html = """
        <!doctype html>
        <html>
            <head><meta charset="utf-8"><title>Admin API Keys</title></head>
            <body>
                <h1>Admin: API Keys</h1>
                <p>Use the controls below to list, create and revoke API keys.</p>
                <label>Admin Token: <input id="admintoken" type="password" /></label>
                <button onclick="init();">Init</button>
                <button onclick="listKeys()">List Keys</button>
                <pre id="out"></pre>
                <h2>Create</h2>
                <label>Project ID: <input id="proj" /></label>
                <label>Limit (/min): <input id="limit" value="0" /></label>
                <button onclick="createKey()">Create</button>
                <h2>Revoke</h2>
                <label>ApiKey ID: <input id="revokeid" /></label>
                <button onclick="revokeKey()">Revoke</button>
                <h2>Audit Search</h2>
                <label>Action: <input id="filter_action" /></label>
                <label>Actor: <input id="filter_actor" /></label>
                <label>Target Type: <input id="filter_target_type" /></label>
                <label>Target ID: <input id="filter_target_id" /></label>
                <label>Per Page: <input id="per_page" value="10" /></label>
                <button onclick="searchAudit(1)">Search</button>
                <div><button onclick="prevPage()">Prev</button> <button onclick="nextPage()">Next</button></div>
                                <style>
                                    body { font-family: Arial, sans-serif; margin: 20px; }
                                    input { margin: 4px; }
                                    button { margin: 6px; }
                                    pre { background:#f6f8fa; padding:10px; border-radius:4px }
                                </style>
                                <script>
                                let CSRF = null;
                                async function init(){
                                    const token = document.getElementById('admintoken').value;
                                    if(!token) return alert('Enter admin token then Init');
                                    // fetch CSRF token from server
                                    const res = await fetch('/admin/apikeys/csrf', {headers: {'X-ADMIN-TOKEN': token}});
                                    if(!res.ok){ document.getElementById('out').textContent = await res.text(); return; }
                                    const j = await res.json();
                                    CSRF = j.csrf;
                                    document.getElementById('out').textContent = 'CSRF acquired';
                                }
                                function getCookie(name){
                                    const v = document.cookie.match('(^|;) ?'+name+'=([^;]*)(;|$)');
                                    return v? v[2] : null;
                                }
                                async function fetchAdmin(path, opts) {
                                                const token = document.getElementById('admintoken').value;
                                                const csrf = CSRF || getCookie('admin_csrf');
                                                const headers = {'X-ADMIN-TOKEN': token, 'Content-Type': 'application/json', 'X-CSRF-TOKEN': csrf};
                                                const res = await fetch(path, Object.assign({headers}, opts));
                                                const text = await res.text();
                                                document.getElementById('out').textContent = text;
                                }
                                function listKeys(){ fetchAdmin('/admin/apikeys/'); }
                                function createKey(){ const p = document.getElementById('proj').value; const l = document.getElementById('limit').value||0; fetchAdmin(`/admin/apikeys/create?project_id=${p}&rate_limit_per_minute=${l}`, {method:'POST'}); }
                                function revokeKey(){ const id = document.getElementById('revokeid').value; fetchAdmin(`/admin/apikeys/revoke?api_key_id=${id}`, {method:'POST'}); }
                                let auditPage = 1;
                                async function searchAudit(page){
                                    auditPage = page || auditPage;
                                    const token = document.getElementById('admintoken').value;
                                    const action = document.getElementById('filter_action').value;
                                    const actor = document.getElementById('filter_actor').value;
                                    const ttype = document.getElementById('filter_target_type').value;
                                    const tid = document.getElementById('filter_target_id').value;
                                    const per = document.getElementById('per_page').value || 10;
                                    const params = new URLSearchParams({page: auditPage, per_page: per});
                                    if(action) params.append('action', action);
                                    if(actor) params.append('actor', actor);
                                    if(ttype) params.append('target_type', ttype);
                                    if(tid) params.append('target_id', tid);
                                    const res = await fetch('/admin/apikeys/audit/search?'+params.toString(), {headers: {'X-ADMIN-TOKEN': token}});
                                    const j = await res.json();
                                    document.getElementById('out').textContent = JSON.stringify(j, null, 2);
                                }
                                function nextPage(){ auditPage++; searchAudit(auditPage); }
                                function prevPage(){ if(auditPage>1) auditPage--; searchAudit(auditPage); }
                                </script>
            </body>
        </html>
        """
        resp = HTMLResponse(content=html)
        # Harden CSRF cookie: samesite=Lax and secure in production
        secure_flag = True if os.environ.get('ENV') == 'production' or os.environ.get('REQUIRE_HTTPS_FOR_ADMIN', '1') == '1' else False
        resp.set_cookie('admin_csrf', token, httponly=False, secure=secure_flag, samesite='Lax')
        return resp
