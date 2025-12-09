import asyncio
import logging
from typing import Dict, Any, Optional

from executor.docker_manager import get_docker_manager, DockerManager
from api.models.models import Function, Execution

logger = logging.getLogger(__name__)


class ExecutionTimeout(Exception):
    """Raised when execution exceeds timeout."""
    pass


class RuntimeEngine:
    """Manages function execution lifecycle."""
    
    def __init__(self, docker_manager: Optional[DockerManager] = None):
        """Initialize runtime engine."""
        self.docker_manager = docker_manager or get_docker_manager()
    
    async def execute_function(
        self,
        function: Function,
        execution: Execution,
        input_data: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Execute a function in isolated container.
        
        Args:
            function: Function object
            execution: Execution object to track progress
            input_data: Input data to pass to function
        
        Returns:
            Execution result with output, logs, and metadata
        """
        container = None
        execution_result = {
            "success": False,
            "output": None,
            "error": None,
            "execution_time": 0,
            "memory_used": 0,
            "logs": []
        }
        
        try:
            logger.info(f"Starting execution {execution.id} for function {function.id}")
            
            # Create container
            container = await self.docker_manager.create_container(
                runtime=function.runtime,
                memory_limit=function.memory_limit,
                timeout=function.timeout,
                environment_vars=function.environment_vars,
                network_disabled=True
            )
            
            # Start container
            await self.docker_manager.start_container(container)
            
            # Execute code with timeout
            try:
                result = await asyncio.wait_for(
                    self.docker_manager.execute_code(
                        container=container,
                        code=function.code,
                        handler=function.handler,
                        input_data=input_data
                    ),
                    timeout=function.timeout
                )
                
                execution_result.update(result)
                
                # Get container stats
                stats = await self.docker_manager.get_container_stats(container)
                execution_result["memory_used"] = stats.get("memory_used_mb", 0)
                
            except asyncio.TimeoutError:
                logger.warning(f"Execution {execution.id} timed out after {function.timeout}s")
                execution_result["error"] = f"Execution timed out after {function.timeout} seconds"
                execution_result["success"] = False
                raise ExecutionTimeout(f"Execution timed out after {function.timeout} seconds")
            
            logger.info(f"Execution {execution.id} completed successfully")
            
        except ExecutionTimeout as e:
            execution_result["error"] = str(e)
            execution_result["success"] = False
            
        except Exception as e:
            logger.error(f"Error during execution {execution.id}: {e}", exc_info=True)
            execution_result["error"] = str(e)
            execution_result["success"] = False
            
        finally:
            # Cleanup container
            if container:
                try:
                    await self.docker_manager.stop_container(container, timeout=5)
                    await self.docker_manager.remove_container(container)
                except Exception as e:
                    logger.error(f"Failed to cleanup container: {e}")
        
        return execution_result
    
    async def validate_function_code(self, code: str, runtime: str) -> Dict[str, Any]:
        """
        Validate function code for syntax errors.
        
        Args:
            code: Function code to validate
            runtime: Python runtime version
        
        Returns:
            Validation result
        """
        try:
            # Compile code to check for syntax errors
            compile(code, '<string>', 'exec')
            
            return {
                "valid": True,
                "errors": []
            }
        except SyntaxError as e:
            return {
                "valid": False,
                "errors": [
                    {
                        "line": e.lineno,
                        "message": e.msg,
                        "text": e.text
                    }
                ]
            }
        except Exception as e:
            return {
                "valid": False,
                "errors": [
                    {
                        "message": str(e)
                    }
                ]
            }
    
    async def test_function(
        self,
        function: Function,
        test_input: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Test a function without creating an execution record.
        
        Args:
            function: Function to test
            test_input: Test input data
        
        Returns:
            Test result
        """
        container = None
        
        try:
            # Create temporary container
            container = await self.docker_manager.create_container(
                runtime=function.runtime,
                memory_limit=function.memory_limit,
                timeout=min(function.timeout, 10),  # Max 10s for tests
                environment_vars=function.environment_vars,
                network_disabled=True
            )
            
            await self.docker_manager.start_container(container)
            
            # Execute code
            result = await asyncio.wait_for(
                self.docker_manager.execute_code(
                    container=container,
                    code=function.code,
                    handler=function.handler,
                    input_data=test_input
                ),
                timeout=10
            )
            
            return {
                "success": True,
                "result": result
            }
            
        except asyncio.TimeoutError:
            return {
                "success": False,
                "error": "Test execution timed out"
            }
        except Exception as e:
            return {
                "success": False,
                "error": str(e)
            }
        finally:
            if container:
                try:
                    await self.docker_manager.stop_container(container, timeout=2)
                    await self.docker_manager.remove_container(container)
                except Exception as e:
                    logger.error(f"Failed to cleanup test container: {e}")


# Singleton instance
_runtime_engine: Optional[RuntimeEngine] = None


def get_runtime_engine() -> RuntimeEngine:
    """Get or create runtime engine instance."""
    global _runtime_engine
    if _runtime_engine is None:
        _runtime_engine = RuntimeEngine()
    return _runtime_engine