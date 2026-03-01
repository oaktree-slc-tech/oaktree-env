import os, logging, datetime, json, mimetypes, hashlib, tempfile, asyncio, \
	concurrent, uvicorn
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from typing import List, Union, Optional
from typing_extensions import Annotated
from io import BytesIO

from starlette.concurrency import run_in_threadpool
from starlette.middleware.sessions import SessionMiddleware

from fastapi import FastAPI, Depends, HTTPException, Request
from fastapi.openapi.docs import get_swagger_ui_html

from client.utils.general import first
from client.utils.conversion import str2bool

from sonador.apisettings import SONADOR_URL as SONADORENV_URL, \
	SONADOR_APITOKEN as SONADORENV_APITOKEN_ENV, \
	SONADOR_IMAGING_SERVER as SONADORENV_IMAGING_SERVER, \
	SONADOR_SERVICE_CLIENT_ID as SONADORENV_SERVICE_CLIENT_ID, \
	DCMHEADER_MODALITIES_IN_STUDY, DCM_DATE_STRFORMAT, \
	DCM_QUERY_NULL, DCM_QUERY_NOT_NULL, DCM_MODALITY_SR, \
	IMAGING_SERVER_MODIFIED
from sonador.apisettings import DCMHEADER_SERVICE_EPISODE_ID, DCMHEADER_PATIENT_NAME, DCMHEADER_PATIENT_BIRTHDATE
from sonador.apisettings.media import DCMEDIA_M3D_MODALITY
from sonador.servers import SonadorServer
from sonador.serialization import SonadorJsonEncoder
from sonador.helpers import dcm_datetime2rangestr

from sonador_fastapi import validate as sonador_fastapi_validate
from sonador_fastapi import oauth as sonador_oauth

logger = logging.getLogger(__name__)


# Application Folder and 
ROOT,_ = os.path.split(__file__)
APP_NAME = 'Context-Augmentation'
APP_VERSION = '0.1'


# Load Sonador connection variables from environment and validate FastAPI <-> Sonador integration
ISERVER, SONADOR_DATASERVICE = sonador_fastapi_validate.validate_sonadorenv_connection_params(APP_NAME)
FASTAPI_CONF = sonador_fastapi_validate.validate_fastapi_integration_params(APP_NAME, ISERVER, SONADOR_DATASERVICE)


# Initialize FastAPI application
with open(os.path.join(ROOT, 'README.md')) as f:
	logger.warning('%s: %s' % (APP_NAME, APP_VERSION))
	app = FastAPI(title=APP_NAME, version=APP_VERSION, description=f.read(), docs_url=None, redoc_url=None)


# Add session middleware
app.add_middleware(SessionMiddleware, 
	secret_key=FASTAPI_CONF.FASTAPI_APP_ENCRYPTION_SECRET, 
	same_site=FASTAPI_CONF.FASTAPI_SAME_SITE,
	https_only=FASTAPI_CONF.FASTAPI_HTTPS_ONLY)

# Application startup/shutdown events
@app.on_event('startup')
async def app_startup():
	'''	Initialize background workers and process queues
	'''
	app.state.background = ThreadPoolExecutor(max_workers=FASTAPI_CONF.FASTAPI_BACKGROUND_WORKERS)

@app.on_event('shutdown')
async def app_shutdown():
	'''	Stop global background workers
	'''
	app.state.background.shutdown()


def get_background_queue():
	return app.state.background


# Initialize OpenID Connect workflow endpoints
sonador_oidc_client = sonador_oauth.SonadorFastAPIOidcClient(SONADOR_DATASERVICE, FASTAPI_CONF.FASTAPI_APP_ENCRYPTION_SECRET)
sonador_oauth.init_oidc_endpoints(app, sonador_oidc_client)


# Add OpenID protection in front of /docs to prevent un-authorized access.
@app.get('/docs', include_in_schema=False)
async def contextdb_docs(request: Request, user=Depends(sonador_oidc_client.ui_authtoken_check)):
	'''	Retrieve Swagger UI HTML
	'''
	return get_swagger_ui_html(openapi_url='/openapi.json', title='%s Documentation' % APP_NAME)