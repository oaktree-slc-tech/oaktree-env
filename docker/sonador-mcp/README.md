# Sonador RAG MCP Server

MCP server for the Knee MRI Segmentation QA notebook demo (`sonador-agentic.decision-agent-poc.ipynb`).

## What it does

- Stores/retrieves gold standard images and NIfTI segmentations (in-memory)
- Stores assessment embeddings in Orthanc pgvector
- Vector similarity search for past assessments

## Requirements

Docker Model Runner with `ai/mxbai-embed-large`:

```bash
docker model pull ai/mxbai-embed-large
```

## Usage

```bash
cd agentic-ai/fastMCP
docker compose up -d
```

Server runs at `http://localhost:6767/mcp`

## MCP Tools

| Tool | Purpose |
|------|---------|
| `store_assessment` | Store assessment with embedding |
| `find_similar_assessments` | Vector similarity search |
| `get_embedding` | Generate text embedding |
| `store_gold_standard_image` | Store overlay image |
| `get_gold_standard_image` | Retrieve overlay image |
| `store_gold_standard_nifti` | Store NIfTI segmentation |
| `get_gold_standard_nifti` | Retrieve NIfTI segmentation |
| `list_gold_standards` | List stored gold standards |

## Environment Variables

| Variable | Default |
|----------|---------|
| `VECTOR_DB_URL` | `http://orthanc:8042` |
| `EMBEDDING_URL` | `http://host.docker.internal:12434/engines/llama.cpp/v1/embeddings` |
| `EMBEDDING_MODEL` | `ai/mxbai-embed-large` |
| `SONADOR_URL` | `http://imaging:8070` |
| `SONADOR_APITOKEN` | `secure-api@sonador-dev` |

## Notes

- Gold standard data is in-memory only (lost on restart)
- Embeddings are padded from 1024 to 1536 dims for Orthanc compatibility

