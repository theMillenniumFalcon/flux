import asyncio
import logging
from typing import Dict, List, Optional, Set
from datetime import datetime, timedelta
from dataclasses import dataclass
from enum import Enum
import docker.models.containers

from executor.docker_manager import get_docker_manager, DockerManager
from api.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


class ContainerState(str, Enum):
    """Container state in the pool."""
    WARMING = "warming"
    READY = "ready"
    ACTIVE = "active"
    COOLDOWN = "cooldown"


@dataclass
class PooledContainer:
    """Represents a container in the pool."""
    container: docker.models.containers.Container
    runtime: str
    state: ContainerState
    created_at: datetime
    last_used: datetime
    execution_count: int = 0


class ContainerPool:
    """
    Manages a pool of warm containers for faster cold starts.
    Implements container reuse and lifecycle management.
    """
    
    def __init__(self, docker_manager: Optional[DockerManager] = None):
        """Initialize container pool."""
        self.docker_manager = docker_manager or get_docker_manager()
        self.pool: Dict[str, List[PooledContainer]] = {}  # runtime -> containers
        self.active_containers: Set[str] = set()  # container IDs currently in use
        self.lock = asyncio.Lock()
        self.max_pool_size = settings.container_pool_size
        self.max_container_age = timedelta(minutes=30)
        self.max_execution_per_container = 50
        self.cooldown_period = timedelta(seconds=5)
    
    async def initialize(self):
        """Initialize the pool with warm containers."""
        logger.info("Initializing container pool...")
        
        runtimes = ["python3.11", "python3.10", "python3.9"]
        
        for runtime in runtimes:
            self.pool[runtime] = []
            
            # Pre-warm containers for each runtime
            for _ in range(min(2, self.max_pool_size)):
                try:
                    await self._create_warm_container(runtime)
                except Exception as e:
                    logger.error(f"Failed to create warm container for {runtime}: {e}")
        
        logger.info(f"Container pool initialized with {sum(len(c) for c in self.pool.values())} containers")
    
    async def _create_warm_container(self, runtime: str) -> PooledContainer:
        """Create a new warm container."""
        try:
            container = await self.docker_manager.create_container(
                runtime=runtime,
                memory_limit=128,  # Default, will be adjusted on use
                timeout=30,
                network_disabled=True
            )
            
            await self.docker_manager.start_container(container)
            
            pooled = PooledContainer(
                container=container,
                runtime=runtime,
                state=ContainerState.READY,
                created_at=datetime.utcnow(),
                last_used=datetime.utcnow()
            )
            
            logger.info(f"Created warm container {container.id[:12]} for runtime {runtime}")
            return pooled
            
        except Exception as e:
            logger.error(f"Failed to create warm container: {e}")
            raise
    
    async def acquire_container(
        self,
        runtime: str,
        memory_limit: int,
        timeout: int
    ) -> Optional[PooledContainer]:
        """
        Acquire a container from the pool or create a new one.
        
        Args:
            runtime: Python runtime version
            memory_limit: Memory limit in MB
            timeout: Execution timeout in seconds
        
        Returns:
            PooledContainer or None
        """
        async with self.lock:
            # Try to get a ready container from pool
            if runtime in self.pool:
                for pooled in self.pool[runtime]:
                    if (pooled.state == ContainerState.READY and 
                        pooled.container.id not in self.active_containers):
                        
                        # Check container age
                        age = datetime.utcnow() - pooled.created_at
                        if age > self.max_container_age:
                            await self._remove_from_pool(pooled)
                            continue
                        
                        # Check execution count
                        if pooled.execution_count >= self.max_execution_per_container:
                            await self._remove_from_pool(pooled)
                            continue
                        
                        # Mark as active
                        pooled.state = ContainerState.ACTIVE
                        pooled.last_used = datetime.utcnow()
                        self.active_containers.add(pooled.container.id)
                        
                        logger.info(f"Reusing container {pooled.container.id[:12]} (executions: {pooled.execution_count})")
                        return pooled
            
            # No available container, create new one
            try:
                container = await self.docker_manager.create_container(
                    runtime=runtime,
                    memory_limit=memory_limit,
                    timeout=timeout,
                    network_disabled=True
                )
                
                await self.docker_manager.start_container(container)
                
                pooled = PooledContainer(
                    container=container,
                    runtime=runtime,
                    state=ContainerState.ACTIVE,
                    created_at=datetime.utcnow(),
                    last_used=datetime.utcnow()
                )
                
                self.active_containers.add(container.id)
                
                logger.info(f"Created new container {container.id[:12]} for execution")
                return pooled
                
            except Exception as e:
                logger.error(f"Failed to acquire container: {e}")
                return None
    
    async def release_container(self, pooled: PooledContainer, reuse: bool = True):
        """
        Release a container back to the pool or remove it.
        
        Args:
            pooled: Container to release
            reuse: Whether to return container to pool for reuse
        """
        async with self.lock:
            self.active_containers.discard(pooled.container.id)
            pooled.execution_count += 1
            
            if not reuse or pooled.execution_count >= self.max_execution_per_container:
                # Remove container
                await self._remove_container(pooled)
                logger.info(f"Removed container {pooled.container.id[:12]} (not reusing)")
                return
            
            # Check pool size
            runtime_pool = self.pool.get(pooled.runtime, [])
            if len(runtime_pool) >= self.max_pool_size:
                # Pool full, remove oldest
                oldest = min(runtime_pool, key=lambda c: c.last_used)
                await self._remove_from_pool(oldest)
            
            # Return to pool
            pooled.state = ContainerState.COOLDOWN
            pooled.last_used = datetime.utcnow()
            
            if pooled.runtime not in self.pool:
                self.pool[pooled.runtime] = []
            
            self.pool[pooled.runtime].append(pooled)
            
            # Schedule cooldown -> ready transition
            asyncio.create_task(self._cooldown_container(pooled))
            
            logger.info(f"Released container {pooled.container.id[:12]} to pool")
    
    async def _cooldown_container(self, pooled: PooledContainer):
        """Transition container from cooldown to ready after cooldown period."""
        await asyncio.sleep(self.cooldown_period.total_seconds())
        
        async with self.lock:
            if pooled in self.pool.get(pooled.runtime, []):
                pooled.state = ContainerState.READY
                logger.debug(f"Container {pooled.container.id[:12]} ready for reuse")
    
    async def _remove_from_pool(self, pooled: PooledContainer):
        """Remove container from pool and clean up."""
        if pooled.runtime in self.pool:
            try:
                self.pool[pooled.runtime].remove(pooled)
            except ValueError:
                pass
        
        await self._remove_container(pooled)
    
    async def _remove_container(self, pooled: PooledContainer):
        """Stop and remove a container."""
        try:
            await self.docker_manager.stop_container(pooled.container, timeout=2)
            await self.docker_manager.remove_container(pooled.container)
            logger.debug(f"Removed container {pooled.container.id[:12]}")
        except Exception as e:
            logger.error(f"Error removing container: {e}")
    
    async def cleanup_old_containers(self):
        """Remove old and unused containers from pool."""
        removed = 0
        
        async with self.lock:
            for runtime, containers in list(self.pool.items()):
                for pooled in list(containers):
                    age = datetime.utcnow() - pooled.created_at
                    idle_time = datetime.utcnow() - pooled.last_used
                    
                    # Remove if too old or idle too long
                    if (age > self.max_container_age or 
                        idle_time > timedelta(minutes=15) or
                        pooled.state == ContainerState.COOLDOWN and idle_time > timedelta(minutes=5)):
                        
                        await self._remove_from_pool(pooled)
                        removed += 1
        
        if removed > 0:
            logger.info(f"Cleaned up {removed} old containers from pool")
        
        return removed
    
    async def maintain_pool(self):
        """Maintain optimal pool size by creating warm containers."""
        async with self.lock:
            for runtime in ["python3.11", "python3.10", "python3.9"]:
                if runtime not in self.pool:
                    self.pool[runtime] = []
                
                ready_count = sum(
                    1 for c in self.pool[runtime] 
                    if c.state == ContainerState.READY
                )
                
                # Maintain minimum ready containers
                min_ready = 2
                if ready_count < min_ready:
                    needed = min_ready - ready_count
                    for _ in range(needed):
                        try:
                            pooled = await self._create_warm_container(runtime)
                            self.pool[runtime].append(pooled)
                        except Exception as e:
                            logger.error(f"Failed to maintain pool: {e}")
                            break
        
        logger.debug("Pool maintenance completed")
    
    async def get_pool_stats(self) -> Dict:
        """Get statistics about the container pool."""
        stats = {
            "total_containers": 0,
            "by_runtime": {},
            "by_state": {state.value: 0 for state in ContainerState},
            "active_count": len(self.active_containers)
        }
        
        async with self.lock:
            for runtime, containers in self.pool.items():
                stats["by_runtime"][runtime] = {
                    "total": len(containers),
                    "ready": sum(1 for c in containers if c.state == ContainerState.READY),
                    "active": sum(1 for c in containers if c.state == ContainerState.ACTIVE),
                    "cooldown": sum(1 for c in containers if c.state == ContainerState.COOLDOWN)
                }
                stats["total_containers"] += len(containers)
                
                for container in containers:
                    stats["by_state"][container.state.value] += 1
        
        return stats
    
    async def shutdown(self):
        """Shutdown pool and cleanup all containers."""
        logger.info("Shutting down container pool...")
        
        async with self.lock:
            for runtime, containers in self.pool.items():
                for pooled in containers:
                    await self._remove_container(pooled)
            
            self.pool.clear()
            self.active_containers.clear()
        
        logger.info("Container pool shutdown complete")


# Singleton instance
_container_pool: Optional[ContainerPool] = None


async def get_container_pool() -> ContainerPool:
    """Get or create container pool instance."""
    global _container_pool
    if _container_pool is None:
        _container_pool = ContainerPool()
        await _container_pool.initialize()
    return _container_pool