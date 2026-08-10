"""
WebSocket routes for Kubernetes operations requiring real-time bidirectional communication.
Handles pod exec (terminal) sessions.
"""
import asyncio
import base64
import json
import logging

from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect
from kubernetes import client
from kubernetes.stream import stream
from sqlalchemy.orm import Session

from database import get_db
from services.kubernetes_service import KubernetesService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ws/k8s", tags=["kubernetes-websocket"])


async def _validate_ws_token(websocket: WebSocket, token: str | None) -> bool:
    """
    Validate a JWT token for WebSocket connections.

    Returns True if valid (or auth is disabled), False otherwise.
    When False the caller should return immediately — the WebSocket has
    already been closed with code 4401.
    """
    from core.config import settings
    if not settings.REQUIRE_AUTH:
        return True

    if not token:
        await websocket.close(code=4401, reason="Unauthorized — token query parameter required")
        return False

    try:
        from services.auth_service import decode_token
        payload = decode_token(token)  # raises UnauthorizedError if invalid/expired
        role = payload.get("role", "")
        if role not in ("admin", "operator", "viewer"):
            await websocket.close(code=4401, reason="Unauthorized — insufficient role")
            return False
        return True
    except Exception:
        await websocket.close(code=4401, reason="Unauthorized — invalid or expired token")
        return False


@router.websocket("/clusters/{cluster_id}/pods/{pod_name}/exec")
async def pod_exec_websocket(
    websocket: WebSocket,
    cluster_id: int,
    pod_name: str,
    namespace: str = Query(...),
    container: str = Query(None),
    command: str = Query(default="/bin/sh"),
    token: str = Query(None),
    db: Session = Depends(get_db)
):
    """
    WebSocket endpoint for pod exec (interactive terminal).
    Equivalent to: kubectl exec -it {pod_name} -- {command}

    Protocol:
        Client → Server: {"type": "stdin", "data": "base64_encoded_input"}
        Server → Client: {"type": "stdout", "data": "base64_encoded_output"}
        Server → Client: {"type": "stderr", "data": "base64_encoded_error"}
        Server → Client: {"type": "exit", "code": 0}
        Client → Server: {"type": "resize", "rows": 24, "cols": 80}

    Query parameters:
        - namespace: Pod namespace (required)
        - container: Container name (optional, uses first container if not specified)
        - command: Shell command to execute (default: /bin/sh)
        - token: JWT token for authentication (required when auth is enabled)

    Example usage from frontend:
        const ws = new WebSocket('ws://localhost:8000/ws/k8s/clusters/1/pods/my-pod/exec?namespace=default&command=/bin/bash&token=eyJ...');
        ws.onmessage = (event) => {
            const msg = JSON.parse(event.data);
            if (msg.type === 'stdout') {
                terminal.write(atob(msg.data));
            }
        };
        ws.send(JSON.stringify({type: 'stdin', data: btoa('ls -la\n')}));
    """
    # Validate JWT before accepting the WebSocket connection
    if not await _validate_ws_token(websocket, token):
        return

    await websocket.accept()

    try:
        # Load Kubernetes API client
        k8s_service = KubernetesService(db)
        cluster = k8s_service.get_cluster(cluster_id)
        api_client = k8s_service.load_kubeconfig(cluster)
        v1 = client.CoreV1Api(api_client)

        logger.info(f"Starting exec session: cluster={cluster.name}, pod={pod_name}, namespace={namespace}, container={container}, command={command}")

        # Create exec stream
        exec_command = command.split()

        # Use kubernetes-client's stream functionality for WebSocket-based exec
        # This creates a bidirectional channel for stdin/stdout/stderr
        resp = stream(
            v1.connect_get_namespaced_pod_exec,
            name=pod_name,
            namespace=namespace,
            container=container,
            command=exec_command,
            stderr=True,
            stdin=True,
            stdout=True,
            tty=True,
            _preload_content=False
        )

        # Send initial connection success message
        await websocket.send_json({
            "type": "connected",
            "message": f"Connected to {pod_name}",
            "pod": pod_name,
            "namespace": namespace,
            "container": container or "default",
            "command": command
        })

        loop = asyncio.get_running_loop()

        # Blocking reads must run in a thread executor so they don't
        # starve the async event loop and block the write task.
        def _read_stdout():
            try:
                return resp.read_stdout(timeout=0.5)
            except Exception:
                return None

        def _read_stderr():
            try:
                return resp.read_stderr(timeout=0.1)
            except Exception:
                return None

        def _is_open():
            return resp.is_open()

        def _get_return_info():
            """Get return code and any error message from the exec stream.

            The kubernetes WSClient stores the exit status on channel 3.
            For containers without a shell (distroless/scratch), the error
            message is the return "code" text and `resp.returncode` raises
            ValueError because it tries int() on the error string.
            """
            return_code = -1
            error_detail = ""
            try:
                resp.update(timeout=1)
                rc = resp.returncode
                return_code = rc if rc is not None else -1
            except ValueError as ve:
                # returncode is a non-integer error string from the container
                # runtime, e.g. "error executing command in container: OCI
                # runtime exec failed: ... executable file not found"
                error_detail = str(ve)
            except Exception:
                pass

            # Also check if channel 3 (status) has error details
            try:
                status_data = resp.read_channel(3)
                if status_data and "exec failed" in status_data.lower():
                    error_detail = status_data
            except Exception:
                pass

            return return_code, error_detail

        # Create tasks for bidirectional communication
        async def read_from_exec():
            """Read output from exec stream and send to WebSocket client."""
            empty_reads = 0
            try:
                while await loop.run_in_executor(None, _is_open):
                    output = await loop.run_in_executor(None, _read_stdout)
                    if output:
                        empty_reads = 0
                        encoded = base64.b64encode(output.encode('utf-8')).decode('ascii')
                        await websocket.send_json({"type": "stdout", "data": encoded})

                    error = await loop.run_in_executor(None, _read_stderr)
                    if error:
                        empty_reads = 0
                        encoded = base64.b64encode(error.encode('utf-8')).decode('ascii')
                        await websocket.send_json({"type": "stderr", "data": encoded})

                    if not output and not error:
                        empty_reads += 1
                        # If we get many empty reads at the very start, the shell
                        # likely doesn't exist or exited immediately.
                        if empty_reads >= 3 and empty_reads <= 5:
                            # Check if stream already closed — command may not exist
                            if not await loop.run_in_executor(None, _is_open):
                                break
                        await asyncio.sleep(0.05)

                # Stream closed — try to get the return code
                return_code, error_detail = await loop.run_in_executor(None, _get_return_info)

                # Detect "no shell" errors from the container runtime
                no_shell_phrases = [
                    "executable file not found",
                    "no such file or directory",
                    "exec failed",
                    "OCI runtime exec failed",
                ]
                is_no_shell = any(phrase in error_detail.lower() for phrase in no_shell_phrases)

                if is_no_shell or return_code in (126, 127):
                    # Container doesn't have the requested shell
                    shell_name = command.split("/")[-1] if "/" in command else command
                    await websocket.send_json({
                        "type": "error",
                        "message": (
                            f"Shell '{shell_name}' is not available in this container. "
                            f"This may be a distroless or minimal container without a shell. "
                            f"Try a different shell (sh, bash, ash) or a different container in this pod."
                        )
                    })
                    logger.info(f"Shell not available: pod={pod_name}, container={container}, command={command}")
                elif return_code != 0 and empty_reads <= 5:
                    # Shell exited immediately with an error
                    detail = f" ({error_detail})" if error_detail else ""
                    await websocket.send_json({
                        "type": "error",
                        "message": f"Shell exited immediately (exit code {return_code}){detail}. The container may not have '{command}'."
                    })
                else:
                    await websocket.send_json({
                        "type": "exit",
                        "code": return_code,
                        "message": "Session terminated"
                    })

            except Exception as e:
                logger.error(f"Error reading from exec stream: {e}")
                try:
                    await websocket.send_json({"type": "error", "message": str(e)})
                except Exception:
                    pass

        async def write_to_exec():
            """Read input from WebSocket client and send to exec stream."""
            try:
                while True:
                    message = await websocket.receive_json()

                    if message.get("type") == "stdin":
                        input_data = base64.b64decode(message.get("data", "")).decode('utf-8')
                        await loop.run_in_executor(None, resp.write_stdin, input_data)

                    elif message.get("type") == "resize":
                        rows = message.get("rows", 24)
                        cols = message.get("cols", 80)
                        try:
                            resize_msg = json.dumps({"Height": rows, "Width": cols})
                            await loop.run_in_executor(None, resp.write_channel, 4, resize_msg)
                        except Exception as resize_err:
                            logger.debug(f"Terminal resize failed: {resize_err}")

                    elif message.get("type") == "close":
                        logger.info("Client requested session close")
                        break

            except WebSocketDisconnect:
                logger.info("WebSocket disconnected by client")
            except Exception as e:
                logger.error(f"Error writing to exec stream: {e}")

        # Run both tasks concurrently
        read_task = asyncio.create_task(read_from_exec())
        write_task = asyncio.create_task(write_to_exec())

        done, pending = await asyncio.wait(
            [read_task, write_task],
            return_when=asyncio.FIRST_COMPLETED
        )

        for task in pending:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        resp.close()
        logger.info(f"Exec session closed: pod={pod_name}")

    except WebSocketDisconnect:
        logger.info(f"WebSocket disconnected: pod={pod_name}")
    except Exception as e:
        logger.error(f"Exec session error: {e}")
        try:
            await websocket.send_json({
                "type": "error",
                "message": f"Exec failed: {str(e)}"
            })
        except Exception:
            pass
    finally:
        try:
            await websocket.close()
        except Exception:
            pass


@router.websocket("/clusters/{cluster_id}/pods/{pod_name}/logs/follow")
async def pod_logs_follow_websocket(
    websocket: WebSocket,
    cluster_id: int,
    pod_name: str,
    namespace: str = Query(...),
    container: str = Query(None),
    tail_lines: int = Query(default=100),
    token: str = Query(None),
    db: Session = Depends(get_db)
):
    """
    WebSocket endpoint for following pod logs in real-time.
    Equivalent to: kubectl logs -f {pod_name}

    Protocol:
        Server → Client: {"type": "log", "data": "log line"}
        Server → Client: {"type": "connected", "message": "..."}
        Server → Client: {"type": "error", "message": "..."}

    Query parameters:
        - namespace: Pod namespace (required)
        - container: Container name (optional)
        - tail_lines: Number of lines to show from end (default: 100)
        - token: JWT token for authentication (required when auth is enabled)
    """
    # Validate JWT before accepting the WebSocket connection
    if not await _validate_ws_token(websocket, token):
        return

    await websocket.accept()

    try:
        # Load Kubernetes API client
        k8s_service = KubernetesService(db)
        cluster = k8s_service.get_cluster(cluster_id)
        api_client = k8s_service.load_kubeconfig(cluster)
        v1 = client.CoreV1Api(api_client)

        logger.info(f"Starting log follow: pod={pod_name}, namespace={namespace}, container={container}")

        # Send connection success
        await websocket.send_json({
            "type": "connected",
            "message": f"Following logs for {pod_name}",
            "pod": pod_name,
            "namespace": namespace,
            "container": container or "default"
        })

        # Stream logs with follow=True.
        # _preload_content=False returns an urllib3.HTTPResponse whose
        # .stream() generator yields raw byte chunks (NOT individual lines).
        log_response = v1.read_namespaced_pod_log(
            name=pod_name,
            namespace=namespace,
            container=container,
            follow=True,
            tail_lines=tail_lines,
            _preload_content=False
        )

        # Create the stream iterator ONCE — calling .stream() inside the
        # loop would create a new generator each time, breaking iteration.
        stream_iter = log_response.stream(decode_content=True)

        # Buffer for incomplete lines (chunks may split mid-line)
        line_buffer = ""

        def _read_next_chunk():
            """Read next chunk from the blocking K8s log stream."""
            try:
                chunk = next(stream_iter)
                return chunk.decode("utf-8") if isinstance(chunk, bytes) else chunk
            except StopIteration:
                return None

        loop = asyncio.get_running_loop()

        while True:
            chunk = await loop.run_in_executor(None, _read_next_chunk)
            if chunk is None:
                # Stream ended — flush any remaining buffer
                if line_buffer:
                    await websocket.send_json({"type": "log", "data": line_buffer})
                break

            # Split chunk into lines, handling partial lines via buffer
            line_buffer += chunk
            lines = line_buffer.split("\n")
            # Last element is either "" (chunk ended with \n) or a partial line
            line_buffer = lines.pop()

            for line in lines:
                if line:  # skip empty strings from consecutive newlines
                    await websocket.send_json({"type": "log", "data": line})

    except WebSocketDisconnect:
        logger.info(f"WebSocket disconnected: pod={pod_name}")
    except Exception as e:
        logger.error(f"Log follow error: {e}")
        try:
            await websocket.send_json({
                "type": "error",
                "message": f"Log follow failed: {str(e)}"
            })
        except Exception:
            pass
    finally:
        # Explicitly close the HTTP stream to release the connection
        try:
            if 'log_response' in locals():
                log_response.close()
        except Exception:
            pass
        try:
            await websocket.close()
        except Exception:
            pass
