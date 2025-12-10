# Flux - Serverless Function Runtime Platform

A production-ready serverless function runtime platform built with Python, FastAPI, Docker, and Celery. Execute Python code in isolated containers with intelligent resource management, container pooling, and comprehensive monitoring.

[![Python](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.104+-green.svg)](https://fastapi.tiangolo.com/)
[![Docker](https://img.shields.io/badge/docker-required-blue.svg)](https://www.docker.com/)

## 🎯 Features

### Core Functionality
- ✅ **Function Management** - Create, update, delete, and manage Python functions
- ✅ **Isolated Execution** - Run code in secure Docker containers
- ✅ **Async Processing** - Celery-powered distributed task queue
- ✅ **Container Pooling** - 2-3x faster cold starts with container reuse
- ✅ **Multiple Runtimes** - Support for Python 3.9, 3.10, 3.11, 3.12
- ✅ **Real-time Logs** - Capture and stream execution logs
- ✅ **Resource Limits** - Configurable memory and timeout limits
- ✅ **Rate Limiting** - Global rate limiting to prevent system overload
- ✅ **Usage Tracking** - Comprehensive system metrics and monitoring
- ✅ **Health Monitoring** - System health checks and diagnostics

### Performance Features
- 🚀 **Fast Execution** - Container pooling reduces cold starts by 51%
- 🔄 **Container Reuse** - Containers used up to 50 times before replacement
- ⚡ **Async Execution** - Non-blocking function execution
- 📊 **Auto-scaling** - Automatic container lifecycle management

## 🏗️ Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                         Client                              │
└───────────────────────────┬─────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────┐
│                    FastAPI Server                           │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐       │
│  │  Functions   │  │  Executions  │  │  Resources   │       │
│  │   Routes     │  │    Routes    │  │    Routes    │       │
│  └──────────────┘  └──────────────┘  └──────────────┘       │
└────────────────────────┬────────────────────────────────────┘
                         │
            ┌────────────┼────────────┐
            ▼            ▼            ▼
    ┌──────────┐  ┌──────────┐  ┌──────────┐
    │PostgreSQL│  │  Redis   │  │  Celery  │
    │          │  │  Cache   │  │  Workers │
    └──────────┘  └──────────┘  └────┬─────┘
                                      │
                                      ▼
                              ┌────────────────┐
                              │ Container Pool │
                              │  ┌──────────┐  │
                              │  │ Python   │  │
                              │  │ 3.9-3.12 │  │
                              │  │Containers│  │
                              │  └──────────┘  │
                              └────────────────┘
```

## 📁 Project Structure

```
flux/
├── api/                            # FastAPI application
│   ├── routes/                     # API endpoints
│   │   ├── functions.py            # Function CRUD operations
│   │   ├── executions.py           # Execution management
│   │   ├── resources.py            # Resource monitoring
│   │   └── health.py               # Health checks
│   ├── models/                     # Database models
│   │   └── models.py               # SQLAlchemy models
│   ├── schemas/                    # Pydantic schemas
│   │   └── schemas.py              # Request/response models
│   ├── config.py                   # Application configuration
│   ├── database.py                 # Database connection
│   ├── redis_client.py             # Redis connection
│   ├── rate_limiter.py             # Global rate limiting
│   ├── quota_manager.py            # Usage tracking
│   ├── middleware.py               # Custom middleware
│   └── main.py                     # FastAPI application
├── executor/                       # Execution engine
│   ├── docker_manager.py           # Docker container management
│   ├── runtime.py                  # Function execution runtime
│   └── container_pool.py           # Container pooling system
├── workers/                        # Celery workers
│   ├── celery_app.py               # Celery configuration
│   └── tasks.py                    # Celery tasks
├── docker/                         # Docker configurations
│   ├── runtime-images/             # Runtime container Dockerfiles
│   │   ├── Dockerfile.python3.9
│   │   ├── Dockerfile.python3.10
│   │   ├── Dockerfile.python3.11
│   │   └── Dockerfile.python3.12
│   ├── Dockerfile.worker           # Celery worker container
│   └── volumes/                    # Docker volumes (created at runtime)
├── scripts/                        # Utility scripts
│   └── build_runtime_images.sh     # Build all runtime images
├── alembic/                        # Database migrations
│   └── versions/                   # Migration files
├── .env.example                    # Environment variables template
├── docker-compose.yml              # Docker Compose configuration
├── pyproject.toml                  # Python dependencies (uv)
└── README.md                       # This file
```

## 🚀 Quick Start

### Prerequisites

- Python 3.11+
- Docker Desktop
- uv package manager
- Git

### Installation

1. **Clone and Initialize**
```bash
# Create project directory
mkdir flux && cd flux

# Initialize with uv
uv init .

# Create directory structure (automated in scripts)
```

2. **Install Dependencies**
```bash
uv sync
```

3. **Configure Environment**
```bash
cp .env.example .env
# Edit .env with your settings
```

4. **Start Infrastructure**
```bash
docker-compose up -d postgres redis
```

5. **Initialize Database**
```bash
uv run alembic upgrade head
```

6. **Build Runtime Images**
```bash
bash scripts/build_runtime_images.sh
```

7. **Start Services**
```bash
# Terminal 1: API Server
uv run uvicorn api.main:app --reload

# Terminal 2: Celery Worker
uv run celery -A workers.celery_app worker --loglevel=info --concurrency=4

# Terminal 3: Celery Beat (periodic tasks)
uv run celery -A workers.celery_app beat --loglevel=info
```

8. **Test the System**
```bash
# Run test suite
python scripts/test_examples.py

# Check health
curl http://localhost:8000/api/v1/health
```

## 📖 API Documentation

Once running, visit:
- **Swagger UI**: http://localhost:8000/docs
- **ReDoc**: http://localhost:8000/redoc

### Core Endpoints

#### Functions
```bash
POST   /api/v1/functions              # Create function
GET    /api/v1/functions              # List functions
GET    /api/v1/functions/{id}         # Get function
PUT    /api/v1/functions/{id}         # Update function
DELETE /api/v1/functions/{id}         # Delete function
GET    /api/v1/functions/{id}/stats   # Get statistics
POST   /api/v1/functions/{id}/validate # Validate code
POST   /api/v1/functions/{id}/test    # Test function
```

#### Executions
```bash
POST   /api/v1/executions/{function_id}/execute  # Execute function
GET    /api/v1/executions/{id}                   # Get execution
GET    /api/v1/executions                        # List executions
GET    /api/v1/executions/{id}/logs             # Get logs
DELETE /api/v1/executions/{id}                   # Cancel execution
```

#### Resources
```bash
GET    /api/v1/resources/usage          # System usage stats
GET    /api/v1/resources/rate-limit     # Rate limit status
POST   /api/v1/resources/rate-limit/reset # Reset rate limit
GET    /api/v1/resources/limits         # System limits
GET    /api/v1/resources/pool/stats     # Pool statistics
POST   /api/v1/resources/pool/maintain  # Maintain pool
GET    /api/v1/resources/metrics/system # System metrics
```

#### Health
```bash
GET    /api/v1/health                   # Health check
GET    /metrics                         # App metrics
```

## 💡 Usage Examples

### Create and Execute a Function

```bash
# 1. Create a function
curl -X POST http://localhost:8000/api/v1/functions \
  -H "Content-Type: application/json" \
  -d '{
    "name": "hello_world",
    "code": "def main(event):\n    name = event.get(\"name\", \"World\")\n    return {\"message\": f\"Hello, {name}!\"}",
    "handler": "main",
    "runtime": "python3.11",
    "memory_limit": 128,
    "timeout": 5
  }'

# Response: {"id": "func_abc123", ...}

# 2. Execute the function
curl -X POST http://localhost:8000/api/v1/executions/func_abc123/execute \
  -H "Content-Type: application/json" \
  -d '{
    "input_data": {"name": "Flux"},
    "async_execution": false
  }'

# Response: {"id": "exec_xyz789", "status": "completed", "output_data": {"message": "Hello, Flux!"}, ...}
```

### Check System Status

```bash
# Pool statistics
curl http://localhost:8000/api/v1/resources/pool/stats

# System usage
curl http://localhost:8000/api/v1/resources/usage

# Rate limit status
curl http://localhost:8000/api/v1/resources/rate-limit
```

## ⚙️ Configuration

### System Limits

Edit `api/rate_limiter.py`:
```python
self.max_requests_per_hour = 1000      # Global rate limit
self.max_concurrent_executions = 20     # Max concurrent
```

Edit `api/config.py` or `.env`:
```bash
MAX_EXECUTION_TIME_PRO=30              # Max timeout (seconds)
MAX_MEMORY_LIMIT_PRO=512               # Max memory (MB)
CONTAINER_POOL_SIZE=5                  # Containers per runtime
LOG_RETENTION_DAYS=7                   # Log retention
```

### Container Pool

Edit `executor/container_pool.py`:
```python
self.max_pool_size = 5                          # Pool size
self.max_container_age = timedelta(minutes=30)  # Max age
self.max_execution_per_container = 50           # Reuse limit
```

### Manual Testing

```bash
# Health check
curl http://localhost:8000/api/v1/health

# Create simple function
curl -X POST http://localhost:8000/api/v1/functions \
  -H "Content-Type: application/json" \
  -d '{"name":"test","code":"def main(e): return {\"ok\": True}","handler":"main","runtime":"python3.11"}'

# Execute it
curl -X POST http://localhost:8000/api/v1/executions/{function_id}/execute \
  -H "Content-Type: application/json" \
  -d '{"input_data":{},"async_execution":false}'
```

## 📊 Performance

### Container Pooling Benefits

| Metric | Without Pool | With Pool | Improvement |
|--------|-------------|-----------|-------------|
| Cold Start | 3.5s | 3.5s | - |
| Warm Start | 3.5s | 0.8s | **76%** |
| Average | 3.5s | 1.7s | **51%** |

### Resource Efficiency
- ✅ 2-3x faster execution after first run
- ✅ Reduced Docker API calls
- ✅ Lower memory overhead
- ✅ Better resource utilization

## 🔐 Security Features

- ✅ Isolated container execution
- ✅ Non-root user in containers
- ✅ Read-only filesystem (except /tmp)
- ✅ No privilege escalation
- ✅ Resource limits (CPU, memory)
- ✅ Network isolation (optional)
- ✅ Rate limiting

## 📈 Monitoring

### Key Metrics

```bash
# System health
curl http://localhost:8000/api/v1/health

# Pool statistics
curl http://localhost:8000/api/v1/resources/pool/stats

# Usage metrics
curl http://localhost:8000/api/v1/resources/usage

# Function statistics
curl http://localhost:8000/api/v1/functions/{id}/stats
```

### Logs

```bash
# API logs (in terminal running uvicorn)

# Worker logs
docker-compose logs -f celery_worker

# Database
docker-compose exec postgres psql -U flux_user -d flux_db

# Redis
docker-compose exec redis redis-cli
```

## 🚢 Deployment

### Docker Compose (Recommended)

```bash
# Build all services
docker-compose build

# Start all services
docker-compose up -d

# View logs
docker-compose logs -f

# Stop services
docker-compose down
```

## 🛠️ Technology Stack

- **API Framework**: FastAPI 0.104+
- **Database**: PostgreSQL 16
- **Cache**: Redis 7
- **Task Queue**: Celery 5.3+
- **Containerization**: Docker 24+
- **Package Manager**: uv
- **Python**: 3.11+