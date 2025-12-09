import docker
from docker.models.containers import Container
from docker.errors import DockerException, NotFound
import logging
from typing import Optional, Dict, Any
from datetime import datetime
import asyncio
from concurrent.futures import ThreadPoolExecutor

from api.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

# Thread pool for blocking Docker operations
executor_pool = ThreadPoolExecutor(max_workers=10)


class DockerManager:
    """Manages Docker containers for function execution."""
    
    def __init__(self):
        """Initialize Docker client."""
        try:
            self.client = docker.DockerClient(base_url=settings.docker_host)
            self.client.ping()
            logger.info("Docker client initialized successfully")
        except DockerException as e:
            logger.error(f"Failed to initialize Docker client: {e}")
            raise
    
    def _get_runtime_image(self, runtime: str) -> str:
        """Get Docker image name for runtime."""
        runtime_images = {
            "python3.9": "flux-runtime:python3.9",
            "python3.10": "flux-runtime:python3.10",
            "python3.11": "flux-runtime:python3.11",
            "python3.12": "flux-runtime:python3.12",
        }
        return runtime_images.get(runtime, "flux-runtime:python3.11")
    
    def _calculate_memory_limit(self, memory_mb: int) -> str:
        """Convert memory limit to Docker format."""
        return f"{memory_mb}m"
    
    async def create_container(
        self,
        runtime: str,
        memory_limit: int,
        timeout: int,
        environment_vars: Optional[Dict[str, str]] = None,
        network_disabled: bool = True
    ) -> Container:
        """
        Create a new container for function execution.
        
        Args:
            runtime: Python runtime version
            memory_limit: Memory limit in MB
            timeout: Execution timeout in seconds
            environment_vars: Environment variables to pass
            network_disabled: Whether to disable network access
        
        Returns:
            Docker container instance
        """
        image = self._get_runtime_image(runtime)
        memory = self._calculate_memory_limit(memory_limit)
        
        # Prepare environment variables
        env_vars = environment_vars or {}
        env_vars.update({
            "PYTHONUNBUFFERED": "1",
            "EXECUTION_TIMEOUT": str(timeout)
        })
        
        # Container configuration
        container_config = {
            "image": image,
            "detach": True,
            "mem_limit": memory,
            "memswap_limit": memory,  # Prevent swap usage
            "cpu_quota": 50000,  # 50% of one CPU core
            "network_disabled": network_disabled,
            "remove": False,  # We'll remove manually after getting logs
            "environment": env_vars,
            "working_dir": "/workspace",
            "user": "sandbox",  # Non-root user
            "read_only": False,  # Allow writing to /tmp
            "tmpfs": {"/tmp": "size=100m"},  # Temporary filesystem
            "security_opt": ["no-new-privileges"],  # Prevent privilege escalation
            "cap_drop": ["ALL"],  # Drop all capabilities
            "pids_limit": 100,  # Limit number of processes
        }
        
        try:
            # Run in thread pool since Docker SDK is synchronous
            loop = asyncio.get_event_loop()
            container = await loop.run_in_executor(
                executor_pool,
                lambda: self.client.containers.create(**container_config)
            )
            logger.info(f"Created container {container.id[:12]} with runtime {runtime}")
            return container
        except DockerException as e:
            logger.error(f"Failed to create container: {e}")
            raise
    
    async def start_container(self, container: Container) -> None:
        """Start a container."""
        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(executor_pool, container.start)
            logger.info(f"Started container {container.id[:12]}")
        except DockerException as e:
            logger.error(f"Failed to start container: {e}")
            raise
    
    async def execute_code(
        self,
        container: Container,
        code: str,
        handler: str,
        input_data: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Execute code in container.
        
        Args:
            container: Docker container
            code: Function code to execute
            handler: Entry point function name
            input_data: Input data to pass to function
        
        Returns:
            Execution result with output, logs, and metadata
        """
        # Create execution wrapper script
        wrapper_script = self._create_wrapper_script(code, handler, input_data)
        
        try:
            loop = asyncio.get_event_loop()
            
            # Copy script to container
            import tarfile
            import io
            
            def create_tar():
                tar_stream = io.BytesIO()
                tar = tarfile.open(fileobj=tar_stream, mode='w')
                script_data = wrapper_script.encode('utf-8')
                tarinfo = tarfile.TarInfo(name='execute.py')
                tarinfo.size = len(script_data)
                tarinfo.mtime = datetime.now().timestamp()
                tar.addfile(tarinfo, io.BytesIO(script_data))
                tar.close()
                tar_stream.seek(0)
                return tar_stream.read()
            
            tar_data = await loop.run_in_executor(executor_pool, create_tar)
            await loop.run_in_executor(
                executor_pool,
                lambda: container.put_archive('/workspace', tar_data)
            )
            
            # Execute the script
            exec_result = await loop.run_in_executor(
                executor_pool,
                lambda: container.exec_run(
                    "python /workspace/execute.py",
                    demux=True,
                    stream=False
                )
            )
            
            exit_code = exec_result.exit_code
            stdout = exec_result.output[0].decode('utf-8') if exec_result.output[0] else ""
            stderr = exec_result.output[1].decode('utf-8') if exec_result.output[1] else ""
            
            # Parse output
            result = self._parse_execution_output(stdout, stderr, exit_code)
            
            return result
            
        except Exception as e:
            logger.error(f"Error executing code: {e}")
            return {
                "success": False,
                "output": None,
                "error": str(e),
                "logs": [],
                "exit_code": -1
            }
    
    def _create_wrapper_script(
        self,
        code: str,
        handler: str,
        input_data: Optional[Dict[str, Any]]
    ) -> str:
        """Create wrapper script for code execution."""
        import json
        
        input_json = json.dumps(input_data or {})
        
        wrapper = f'''
import sys
import json
import traceback
import time
from io import StringIO

# Capture stdout/stderr
original_stdout = sys.stdout
original_stderr = sys.stderr
stdout_capture = StringIO()
stderr_capture = StringIO()

sys.stdout = stdout_capture
sys.stderr = stderr_capture

start_time = time.time()

# User code
user_code = """
{code}
"""

result = {{
    "success": False,
    "output": None,
    "error": None,
    "logs": [],
    "execution_time": 0
}}

try:
    # Execute user code
    exec_globals = {{}}
    exec(user_code, exec_globals)
    
    # Get handler function
    if "{handler}" not in exec_globals:
        raise Exception(f"Handler function '{{handler}}' not found in code")
    
    handler_func = exec_globals["{handler}"]
    
    # Execute handler
    input_data = {input_json}
    output = handler_func(input_data)
    
    result["success"] = True
    result["output"] = output
    
except Exception as e:
    result["error"] = str(e)
    result["traceback"] = traceback.format_exc()

finally:
    # Restore stdout/stderr
    sys.stdout = original_stdout
    sys.stderr = original_stderr
    
    # Calculate execution time
    result["execution_time"] = time.time() - start_time
    
    # Capture logs
    stdout_content = stdout_capture.getvalue()
    stderr_content = stderr_capture.getvalue()
    
    if stdout_content:
        result["logs"].append({{"level": "info", "message": stdout_content}})
    if stderr_content:
        result["logs"].append({{"level": "error", "message": stderr_content}})
    
    # Print result as JSON
    print("<<<FLUX_RESULT_START>>>")
    print(json.dumps(result))
    print("<<<FLUX_RESULT_END>>>")
'''
        return wrapper
    
    def _parse_execution_output(
        self,
        stdout: str,
        stderr: str,
        exit_code: int
    ) -> Dict[str, Any]:
        """Parse execution output to extract result."""
        import json
        
        try:
            # Extract JSON result from stdout
            if "<<<FLUX_RESULT_START>>>" in stdout and "<<<FLUX_RESULT_END>>>" in stdout:
                start_marker = "<<<FLUX_RESULT_START>>>"
                end_marker = "<<<FLUX_RESULT_END>>>"
                
                start_idx = stdout.find(start_marker) + len(start_marker)
                end_idx = stdout.find(end_marker)
                
                result_json = stdout[start_idx:end_idx].strip()
                result = json.loads(result_json)
                
                return result
            else:
                # No proper result found
                return {
                    "success": False,
                    "output": None,
                    "error": "No execution result found",
                    "logs": [
                        {"level": "info", "message": stdout},
                        {"level": "error", "message": stderr}
                    ],
                    "exit_code": exit_code
                }
        except json.JSONDecodeError as e:
            return {
                "success": False,
                "output": None,
                "error": f"Failed to parse result: {e}",
                "logs": [
                    {"level": "info", "message": stdout},
                    {"level": "error", "message": stderr}
                ],
                "exit_code": exit_code
            }
    
    async def get_container_stats(self, container: Container) -> Dict[str, Any]:
        """Get container resource usage statistics."""
        try:
            loop = asyncio.get_event_loop()
            stats = await loop.run_in_executor(
                executor_pool,
                lambda: container.stats(stream=False)
            )
            
            # Extract memory usage
            memory_stats = stats.get('memory_stats', {})
            memory_usage = memory_stats.get('usage', 0)
            memory_limit = memory_stats.get('limit', 1)
            memory_percent = (memory_usage / memory_limit) * 100 if memory_limit > 0 else 0
            memory_mb = memory_usage / (1024 * 1024)
            
            return {
                "memory_used_mb": round(memory_mb, 2),
                "memory_percent": round(memory_percent, 2)
            }
        except Exception as e:
            logger.error(f"Failed to get container stats: {e}")
            return {"memory_used_mb": 0, "memory_percent": 0}
    
    async def stop_container(self, container: Container, timeout: int = 10) -> None:
        """Stop a container gracefully."""
        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                executor_pool,
                lambda: container.stop(timeout=timeout)
            )
            logger.info(f"Stopped container {container.id[:12]}")
        except NotFound:
            logger.warning(f"Container {container.id[:12]} not found, may have been removed")
        except Exception as e:
            logger.error(f"Failed to stop container: {e}")
    
    async def remove_container(self, container: Container, force: bool = True) -> None:
        """Remove a container."""
        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                executor_pool,
                lambda: container.remove(force=force)
            )
            logger.info(f"Removed container {container.id[:12]}")
        except NotFound:
            logger.warning(f"Container {container.id[:12]} not found, may have been removed")
        except Exception as e:
            logger.error(f"Failed to remove container: {e}")
    
    async def cleanup_old_containers(self, max_age_minutes: int = 60) -> int:
        """Clean up containers older than specified age."""
        try:
            loop = asyncio.get_event_loop()
            containers = await loop.run_in_executor(
                executor_pool,
                lambda: self.client.containers.list(
                    all=True,
                    filters={"ancestor": "flux-runtime"}
                )
            )
            
            cleaned = 0
            for container in containers:
                try:
                    # Check container age
                    created = datetime.fromisoformat(
                        container.attrs['Created'].replace('Z', '+00:00')
                    )
                    age_minutes = (datetime.now(created.tzinfo) - created).total_seconds() / 60
                    
                    if age_minutes > max_age_minutes:
                        await self.remove_container(container)
                        cleaned += 1
                except Exception as e:
                    logger.error(f"Error cleaning container {container.id[:12]}: {e}")
            
            if cleaned > 0:
                logger.info(f"Cleaned up {cleaned} old containers")
            
            return cleaned
        except Exception as e:
            logger.error(f"Failed to cleanup containers: {e}")
            return 0
    
    def close(self):
        """Close Docker client."""
        if hasattr(self, 'client'):
            self.client.close()
            logger.info("Docker client closed")


# Singleton instance
_docker_manager: Optional[DockerManager] = None


def get_docker_manager() -> DockerManager:
    """Get or create Docker manager instance."""
    global _docker_manager
    if _docker_manager is None:
        _docker_manager = DockerManager()
    return _docker_manager