#!/bin/bash

# Build Flux Runtime Images
# This script builds Docker images for different Python runtimes

set -e

echo "🐳 Building Flux Runtime Images..."

# Colors for output
GREEN='\033[0;32m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Change to project root
cd "$(dirname "$0")/.."

# Create runtime images directory if it doesn't exist
mkdir -p docker/runtime-images

# Python versions to build
PYTHON_VERSIONS=("3.9" "3.10" "3.11" "3.12")

# Create Dockerfiles for each version
for VERSION in "${PYTHON_VERSIONS[@]}"; do
    echo -e "${BLUE}Creating Dockerfile for Python ${VERSION}${NC}"
    
    cat > "docker/runtime-images/Dockerfile.python${VERSION}" << EOF
FROM python:${VERSION}-slim

WORKDIR /workspace

# Install common dependencies
RUN apt-get update && apt-get install -y \\
    gcc \\
    g++ \\
    make \\
    && rm -rf /var/lib/apt/lists/*

# Install commonly used Python packages
RUN pip install --no-cache-dir \\
    requests \\
    numpy \\
    pandas \\
    beautifulsoup4 \\
    pillow \\
    python-dateutil

# Create non-root user for security
RUN useradd -m -u 1000 sandbox && \\
    chown -R sandbox:sandbox /workspace

# Set resource limits will be done at container runtime
USER sandbox

# Set Python to run in unbuffered mode for real-time logs
ENV PYTHONUNBUFFERED=1

# Default command - will be overridden at runtime
CMD ["python", "-c", "print('Flux Runtime Ready')"]
EOF

    echo -e "${BLUE}Building flux-runtime:python${VERSION}${NC}"
    docker build \
        -t "flux-runtime:python${VERSION}" \
        -f "docker/runtime-images/Dockerfile.python${VERSION}" \
        docker/runtime-images/
    
    echo -e "${GREEN}✓ Built flux-runtime:python${VERSION}${NC}"
done

echo ""
echo -e "${GREEN}✓ All runtime images built successfully!${NC}"
echo ""
echo "Available images:"
docker images | grep flux-runtime

echo ""
echo "To test an image, run:"
echo "docker run --rm flux-runtime:python3.11 python --version"