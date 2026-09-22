from __future__ import annotations

from fastapi import FastAPI, HTTPException

from app.analyzer import Analyzer
from app.jobs import JobStore
from app.models import AnalyzeRequest, BatchDownloadRequest, DownloadJob
from app.security import UnsafeUrlError

app = FastAPI(title="Iris", version="0.1.0", description="Universal page analyzer and download manager")
analyzer = Analyzer()
jobs = JobStore()


@app.get("/health")
async def health():
    return {"ok": True, "service": "iris", "version": "0.1.0"}


@app.post("/api/analyze")
async def analyze(request: AnalyzeRequest):
    try:
        return await analyzer.analyze(str(request.url), deep=request.deep)
    except UnsafeUrlError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Falha ao analisar página: {type(exc).__name__}") from exc


@app.post("/api/downloads", response_model=DownloadJob)
async def create_download(request: BatchDownloadRequest):
    if not request.resources:
        raise HTTPException(status_code=400, detail="Nenhum recurso informado")
    if len(request.resources) > 100:
        raise HTTPException(status_code=400, detail="Máximo de 100 recursos por lote")
    if any(resource.drm for resource in request.resources):
        raise HTTPException(status_code=400, detail="O lote contém conteúdo protegido por DRM")
    job = jobs.create(request.resources)
    jobs.launch(job.id)
    return job


@app.get("/api/jobs", response_model=list[DownloadJob])
async def list_jobs():
    return jobs.list()


@app.get("/api/jobs/{job_id}", response_model=DownloadJob)
async def get_job(job_id: str):
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job não encontrado")
    return job


@app.post("/api/jobs/{job_id}/cancel", response_model=DownloadJob)
async def cancel_job(job_id: str):
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job não encontrado")
    jobs.cancel(job_id)
    return job
