"""HTML must revalidate while media keeps its explicit cache policy."""
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, Response
from fastapi.testclient import TestClient

from api.common import SecurityHeadersMiddleware


def test_document_revalidates_without_disabling_media_cache():
    app = FastAPI()
    app.add_middleware(SecurityHeadersMiddleware)

    @app.get('/')
    def document():
        return HTMLResponse('<script src="/asset.js?v=new"></script>')

    @app.get('/video.m4s')
    def segment():
        return Response(b'video', media_type='video/iso.segment',
                        headers={'Cache-Control': 'public, max-age=31536000'})

    with TestClient(app) as client:
        assert client.get('/').headers['cache-control'] == 'no-cache'
        assert client.get('/video.m4s').headers['cache-control'] == 'public, max-age=31536000'
