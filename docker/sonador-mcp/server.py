#!/usr/bin/env python3
""" Sonador-MCP Server
    
    Provides tools for medical imaging workflows and patient matched processes
    related to preoperative planning.
    
    * Use-case 1: asess the quality of segmentations for approval
"""

import json, os, sys, uuid, requests, logging, mimetypes
from typing import Any, Optional
from urllib.parse import quote

from fastmcp import FastMCP

from client import apisettings as client_api

from sonador.apisettings import SONADOR_URL as SONADORENV_URL, \
    SONADOR_APITOKEN as SONADORENV_APITOKEN_ENV, \
    SONADOR_IMAGING_SERVER as SONADORENV_IMAGING_SERVER
from sonador.helpers import initenv_sonador_server

logger = logging.getLogger(__name__)



# Supported Embedding Backends
EMBEDDING_MODEL_TYPE_DOCKER_RUNNER = 'docker-model-runner'
EMBEDDING_MODEL_TYPE_OLLAMA = 'ollama'


# Sonador Configuration
SONADOR_URL = os.getenv(SONADORENV_URL)
SONADOR_APITOKEN = os.getenv(SONADORENV_APITOKEN_ENV)
SONADOR_IMAGESERVER = os.getenv(SONADORENV_IMAGING_SERVER)

if not SONADOR_URL or not SONADOR_APITOKEN:
    raise ValueError('Unable to initialize Sonador server connection, invalid %s or %s'
        % (SONADORENV_URL, SONADORENV_APITOKEN_ENV))

if not SONADOR_IMAGESERVER:
    raise ValueError(('Unable to initialize Sonador server connection, no imaging server specified. '
        + 'Check %s environment variable.') % SONADORENV_IMAGING_SERVER)


# Initialize Sonador Connection
SONADOR_CONN = initenv_sonador_server()
ISERVER = SONADOR_CONN.get_imageserver(SONADOR_IMAGESERVER)


# LLM / Vector Embeddings
SONADOR_CONTEXTDB_URL = os.getenv("SONADOR_CONTEXTDB_URL")
EMBEDDING_URL = os.getenv("EMBEDDING_URL")
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL")
EMBEDDING_BACKEND_TYPE = os.environ.get('EMBEDDING_BACKEND', EMBEDDING_MODEL_TYPE_OLLAMA)

if not SONADOR_CONTEXTDB_URL:
    raise ValueError('Unable to initialize Sonador Context-Augmentation connection, invalid URL')
if not EMBEDDING_URL or not EMBEDDING_MODEL:
    raise ValueError('Unable to initialize connection to the Embedding bend. Check EMBEDDING_URL and EMBEDDING_MODEL '
        + 'environment variables.')

# Ensure the backend type is supported by the MCP server
if not EMBEDDING_BACKEND_TYPE in (EMBEDDING_MODEL_TYPE_DOCKER_RUNNER, EMBEDDING_MODEL_TYPE_OLLAMA):
    raise ValueError('Unable to initialize MCP server, invalid embedding backend type: "%s". Supported: %s' % (
        str(EMBEDDING_BACKEND_TYPE), 
        ','.join('"%s"' % _s for _s in (EMBEDDING_MODEL_TYPE_DOCKER_RUNNER, EMBEDDING_MODEL_TYPE_OLLAMA)),
    ))

# Ensure that EMBEDDING_MODEL is compatible with the backend type. Docker model runner
# uses a namespace prefix that is not required by Ollama.
if '/' in EMBEDDING_MODEL and EMBEDDING_BACKEND_TYPE == EMBEDDING_MODEL_TYPE_OLLAMA:
    raise ValueError('Invalid model-name="%s" for embedding-backend="%s". Ollama does not use namespaced model names.' % (
        EMBEDDING_MODEL, EMBEDDING_BACKEND_TYPE
    ))



# MCP Server Config
MCP_SERVER_APPNAME = os.environ.get('SONADOR_MCP_APPNAME', 'Sonador-MCP')
DEFAULT_GROUP = int(os.environ.get('SONADOR_MCP_DEFAULT_GROUP'))
MCP_HOSTNAME = os.environ.get('SONADOR_MCP_HOSTNAME', '0.0.0.0')
MCP_PORT = int(os.environ.get('SONADOR_MCP_PORT', 6767))


# Embedding Model Version and Target Dimensions
DEFAULT_MODEL_LABEL = EMBEDDING_MODEL
DEFAULT_MODEL_VERSION = os.environ.get('SONADOR_MCP_DEFAULT_MODEL_VERSION', 'v1')
TARGET_DIMS = int(os.environ.get('SONADOR_MCP_VECTOR_TARGET_DIMENSIONS', 1536))



# Model Decisions
MODEL_DECISIONS_APPROVED = 'approved'
MODEL_DECISIONS_REJECTED = 'rejected'
MODEL_DECISIONS_NEEDS_REVIEW = 'needs_review'
MODEL_DECISIONS_SUPPORTED = (
    MODEL_DECISIONS_APPROVED, MODEL_DECISIONS_REJECTED, MODEL_DECISIONS_NEEDS_REVIEW)

MODEL_QUALITY_EXCELLENT = 'excellent'
MODEL_QUALITY_GOOD = 'good'
MDOEL_QUALITY_ACCEPTABLE = 'acceptable'
MODEL_QUALITY_POOR = 'poor'
MODEL_QUALITY_UNACCEPTABLE = 'unacceptable'
MODEL_QUALITY_SUPPORTED = (
    MODEL_QUALITY_EXCELLENT, MODEL_QUALITY_GOOD, MDOEL_QUALITY_ACCEPTABLE, MODEL_QUALITY_POOR, MODEL_QUALITY_UNACCEPTABLE)


# Map quality grades to integer scores (5=best, 1=worst)
QUALITY_GRADE_SCORES = {
    MODEL_QUALITY_EXCELLENT: 5,
    MODEL_QUALITY_GOOD: 4,
    MDOEL_QUALITY_ACCEPTABLE: 3,
    MODEL_QUALITY_POOR: 2,
    MODEL_QUALITY_UNACCEPTABLE: 1
}


# MCP server
mcp = FastMCP(MCP_SERVER_APPNAME)



# Helper methods

def get_orthanc_headers() -> dict:
    ''' Retrieve Orthanc request headers including Authorization and Content-Type
    '''
    _headers = ISERVER.orthanc_request_headers()
    _headers['Content-Type'] = mimetypes.guess_type('*.json')
    
    return _headers



###!!!---     MCP Server Tools      ---!!!###


# Create Embedding

def generate_embedding(text: str) -> list[float]:
    """ Generate embedding via Docker Model Runner, padded to 1536 dims.
    """
    resp = requests.post(EMBEDDING_URL, json={
            "model": EMBEDDING_MODEL, "input": text
    }, timeout=30)
    resp.raise_for_status()
    embedding = resp.json()["data"][0]["embedding"]

    if len(embedding) < TARGET_DIMS:
        embedding = embedding + [0.0] * (TARGET_DIMS - len(embedding))

    norm = sum(x * x for x in embedding) ** 0.5
    if norm > 0:
        embedding = [x / norm for x in embedding]

    return embedding


# Assessment tools

@mcp.tool()
def store_assessment(
    source: str, resource: str, organ_label: str, decision: str, confidence: float, quality_grade: str, reasoning: str,
    issues: Optional[list[str]] = None, recommendations: Optional[list[str]] = None, dice: Optional[float] = None,
    hausdorff: Optional[float] = None, group: Optional[int] = None, 
    model_label: Optional[str] = None, model_version: Optional[str] = None
) -> dict[str, Any]:
    """ Store segmentation assessment with embedding in Context-Agumentation.
    """
    if decision not in DECISIONS:
        return {"status": False, "message": f"Invalid decision: {decision}"}
    if quality_grade not in QUALITY_GRADES:
        return {"status": False, "message": f"Invalid quality_grade: {quality_grade}"}

    # Use defaults if not provided
    group = group if group is not None else DEFAULT_GROUP
    model_label = model_label or DEFAULT_MODEL_LABEL
    model_version = model_version or DEFAULT_MODEL_VERSION

    try:
        embedding = generate_embedding(reasoning)

        # Build notes field with assessment details (serialize to JSON string)
        notes_dict = {
            "organ_label": organ_label,
            "decision": decision,
            "confidence": confidence,
            "quality_grade": quality_grade,
            "reasoning": reasoning,
            "issues": issues or [],
            "recommendations": recommendations or []
        }

        # Convert quality grade to integer score
        quality_score = QUALITY_GRADE_SCORES.get(quality_grade, 3)

        payload = {
            "model_label": model_label,
            "model_version": model_version,
            "embedding": embedding,
            "source": source,
            "resource": resource,
            "quality": quality_score,
            "dice": dice,
            "hausdorff": hausdorff,
            "notes": json.dumps(notes_dict)
        }

        url = f"{SONADOR_CONTEXTDB_URL}/embeddings/{group}/seg"
        headers = get_orthanc_headers()
        resp = requests.post(url, headers=headers, json=payload, timeout=30)

        if resp.status_code in (200, 201):
            result = resp.json()
            uid = result.get("uid")
            return {
                "status": True,
                "uid": uid,
                "group": group,
                "model_label": model_label,
                "model_version": model_version,
                "organ_label": organ_label,
                "decision": decision,
                "message": "Assessment stored"
            }
        else:
            return {
                "status": False,
                "message": f"Orthanc error {resp.status_code}: {resp.text[:200]}"
            }
    except Exception as e:
        return {"status": False, "message": str(e)}


@mcp.tool()
def find_similar_assessments(
    query_text: Optional[str] = None, segmentation_label: Optional[str] = None,
    limit: int = 5,
    group: Optional[int] = None,
    model_label: Optional[str] = None,
    model_version: Optional[str] = None
) -> dict[str, Any]:
    """Find similar assessments via Orthanc vector search."""
    # Use defaults if not provided
    group = group if group is not None else DEFAULT_GROUP
    model_label = model_label or DEFAULT_MODEL_LABEL
    model_version = model_version or DEFAULT_MODEL_VERSION

    try:
        query = query_text or "segmentation quality assessment"
        embedding = generate_embedding(query)

        payload = {
            "embedding": embedding
        }

        url = f"{SONADOR_CONTEXTDB_URL}/embeddings/{group}/seg/{quote(model_label, safe='')}/{quote(model_version, safe='')}/search"
        params = {
            "page": 1,
            "items": limit
        }
        headers = get_orthanc_headers()
        resp = requests.post(url, headers=headers, json=payload, params=params, timeout=30)

        if resp.status_code == 200:
            results = resp.json()
            results = results if isinstance(results, list) else [results] if results else []

            # Parse notes from JSON string to dict for each result
            for r in results:
                notes = r.get("notes")
                if isinstance(notes, str):
                    try:
                        r["notes"] = json.loads(notes)
                    except json.JSONDecodeError:
                        r["notes"] = {}

            # Filter by organ_label if specified
            if segmentation_label and results:
                results = [r for r in results if r.get("notes", {}).get("segmentation_label") == segmentation_label]

            return {
                "status": True,
                "results": results[:limit],
                "count": len(results[:limit]),
                "message": f"Found {len(results)} similar assessments"
            }
        else:
            return {
                "status": False,
                "results": [],
                "count": 0,
                "message": f"Query failed: {resp.status_code}"
            }
    except Exception as e:
        return {"status": False, "results": [], "count": 0, "message": str(e)}


@mcp.tool()
def list_seg_embeddings(
    group: Optional[int] = None,
    model_label: Optional[str] = None,
    model_version: Optional[str] = None,
    page: int = 1,
    items: int = 100
) -> dict[str, Any]:
    """List segmentation vector embeddings, optionally filtered by model_label and model_version."""
    # Use defaults if not provided
    group = group if group is not None else DEFAULT_GROUP
    model_label = model_label or DEFAULT_MODEL_LABEL
    model_version = model_version or DEFAULT_MODEL_VERSION

    try:
        url = f"{SONADOR_CONTEXTDB_URL}/embeddings/{group}/seg/{quote(model_label, safe='')}/{quote(model_version, safe='')}"
        params = {
            "page": page,
            "items": items
        }
        headers = get_orthanc_headers()
        resp = requests.get(url, headers=headers, params=params, timeout=30)

        if resp.status_code == 200:
            results = resp.json()
            results = results if isinstance(results, list) else [results] if results else []
            return {
                "status": True,
                "results": results,
                "count": len(results),
                "message": f"Retrieved {len(results)} embeddings"
            }
        else:
            return {
                "status": False,
                "results": [],
                "count": 0,
                "message": f"Query failed: {resp.status_code}"
            }
    except Exception as e:
        return {"status": False, "results": [], "count": 0, "message": str(e)}


@mcp.tool()
def get_seg_embedding(uid: str, group: Optional[int] = None) -> dict[str, Any]:
    """ Retrieve image segmentation embedding by uid.
    """
    # Use defaults if not provided
    group = group if group is not None else DEFAULT_GROUP

    try:
        url = f"{SONADOR_CONTEXTDB_URL}/embeddings/{group}/seg/{uid}"
        headers = get_orthanc_headers()
        resp = requests.get(url, headers=headers, timeout=30)

        if resp.status_code == 200:
            result = resp.json()
            return {
                "status": True,
                "result": result,
                "message": "Embedding retrieved"
            }
        elif resp.status_code == 404:
            return {
                "status": False,
                "message": f"Embedding uid={uid} does not exist"
            }
        else:
            return {
                "status": False,
                "message": f"Query failed: {resp.status_code}"
            }
    except Exception as e:
        return {"status": False, "message": str(e)}


@mcp.tool()
def update_seg_embedding(group: int, uid: str, source: str, resource: str, 
        model_label: str,model_version: str, embedding: list[float], quality: Optional[str] = None,
        dice: Optional[float] = None, hausdorff: Optional[float] = None, notes: Optional[dict] = None,
) -> dict[str, Any]:
    """ Update image segmentation embedding.
    """
    # Use defaults if not provided
    group = group if group is not None else DEFAULT_GROUP

    try:
        payload = {
            "model_label": model_label,
            "model_version": model_version,
            "embedding": embedding,
            "source": source,
            "resource": resource,
            "quality": quality,
            "dice": dice,
            "hausdorff": hausdorff,
            "notes": notes
        }

        url = f"{SONADOR_CONTEXTDB_URL}/embeddings/{group}/seg/{uid}"
        headers = get_orthanc_headers()
        resp = requests.put(url, headers=headers, json=payload, timeout=30)

        if resp.status_code == 200:
            result = resp.json()
            return {
                "status": True,
                "result": result,
                "message": "Embedding updated"
            }
        elif resp.status_code == 404:
            return {
                "status": False,
                "message": f"Embedding uid={uid} does not exist"
            }
        else:
            return {
                "status": False,
                "message": f"Update failed: {resp.status_code}"
            }
    except Exception as e:
        return {"status": False, "message": str(e)}


@mcp.tool()
def delete_seg_embedding(group: int, uid: str) -> dict[str, Any]:
    """ Delete image segmentation embedding.
    """
    # Use defaults if not provided
    group = group if group is not None else DEFAULT_GROUP

    try:
        url = f"{SONADOR_CONTEXTDB_URL}/embeddings/{group}/seg/{uid}"
        headers = get_orthanc_headers()
        resp = requests.delete(url, headers=headers, timeout=30)

        if resp.status_code == 204:
            return {
                "status": True,
                "uid": uid,
                "message": "Embedding deleted"
            }
        elif resp.status_code == 404:
            return {
                "status": False,
                "message": f"Embedding uid={uid} does not exist"
            }
        else:
            return {
                "status": False,
                "message": f"Delete failed: {resp.status_code}"
            }
    except Exception as e:
        return {"status": False, "message": str(e)}


@mcp.tool()
def get_embedding(text: str) -> dict[str, Any]:
    """ Generate embedding for text.
    """
    try:
        embedding = generate_embedding(text)
        return {
            "status": True,
            "embedding": embedding,
            "dimensions": len(embedding),
            "model": EMBEDDING_MODEL,
        }
    except Exception as e:
        return {"status": False, "message": str(e)}


if __name__ == "__main__":
    STARTUP_LOG = f'''<---   {MCP_SERVER_APPNAME} Startup  --->
Sonador Image Server: "{ISERVER.server_label}"
Context-Augmentation URL: "{SONADOR_CONTEXTDB_URL}"
Emeddings Backend: URL="{EMBEDDING_URL}" backend-type="{EMBEDDING_BACKEND_TYPE}"
Embedding Model: model="{EMBEDDING_MODEL}" model-version="{DEFAULT_MODEL_VERSION}"
Default Sonador Group: {DEFAULT_GROUP}
'''
        
    # Notify user of server start and settings
    logger.warning(STARTUP_LOG)
    mcp.run(transport="streamable-http", host=MCP_HOSTNAME, port=MCP_PORT)
