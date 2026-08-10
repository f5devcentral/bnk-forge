"""
WebSocket endpoint for BNK Operators.

Protocol:
  1. Operator connects to /ws/operator with Bearer token
  2. BNK-Forge validates the token, registers the operator
  3. Bidirectional communication:
     - BNK-Forge → Operator: commands (apply, destroy, scan, etc.)
     - Operator → BNK-Forge: heartbeat, health, command output, results

This follows the same FastAPI WebSocket pattern as:
  - backend/routes/k8s_websocket.py (pod exec)
  - backend/routes/notifications.py (deployment notifications)
  - backend/routes/tasks.py (task streaming)
"""
import asyncio
import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from database import SessionLocal
from services.operator_registry import (
    mark_operator_disconnected,
    operator_connections,
    register_operator,
    update_operator_health,
    update_operator_heartbeat,
    validate_registration_token,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["operator-websocket"])


@router.websocket("/ws/operator")
async def operator_websocket(
    websocket: WebSocket,
    token: str = Query(None),
):
    """
    WebSocket endpoint for BNK operator agents.

    Connection flow:
      1. Operator connects with ?token=bnk_reg_xxx (or Authorization header)
      2. Server validates token, registers operator
      3. Server sends {type: "registered", operator_id: "...", ...}
      4. Bidirectional message loop begins

    Operator → Server messages:
      - {type: "heartbeat", operator_version: "...", uptime_seconds: N, commands_executed: N}
      - {type: "health", cluster: {...}, bnk: {...}}
      - {type: "output", command_id: "...", line: "..."}
      - {type: "result", command_id: "...", success: bool, ...}
      - {type: "error", command_id: "...", error_message: "..."}

    Server → Operator messages:
      - {type: "registered", operator_id: "...", cluster_name: "..."}
      - {type: "command", id: "...", action: "apply_manifests", payload: {...}}
      - {type: "ping"}
    """
    # --- Step 1: Extract token ---
    if not token:
        # Try Authorization header
        auth_header = websocket.headers.get("authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]

    if not token:
        await websocket.close(code=4001, reason="Missing registration token")
        return

    # --- Step 2: Validate token and register operator ---
    db = SessionLocal()
    try:
        token_record = validate_registration_token(db, token)
        if not token_record:
            await websocket.close(code=4003, reason="Invalid or expired registration token")
            return

        # Accept the WebSocket connection
        await websocket.accept()

        # Wait for the operator's registration message (first message must be registration)
        try:
            init_msg = await asyncio.wait_for(websocket.receive_json(), timeout=10.0)
        except TimeoutError:
            await websocket.close(code=4002, reason="Registration timeout — send registration message within 10s")
            return

        if init_msg.get("type") != "register":
            await websocket.close(code=4002, reason="First message must be {type: 'register', cluster_name: '...'}")
            return

        cluster_name = init_msg.get("cluster_name", f"cluster-{token_record.name}")
        operator_version = init_msg.get("operator_version")
        extra_labels = init_msg.get("labels", {})

        # Register in DB
        operator_record = register_operator(
            db=db,
            token=token_record,
            cluster_name=cluster_name,
            operator_version=operator_version,
            extra_labels=extra_labels,
        )
        operator_id = operator_record.operator_id

        # Register in-memory connection
        await operator_connections.register(
            operator_id=operator_id,
            websocket=websocket,
            info={
                "cluster_name": cluster_name,
                "operator_version": operator_version,
                "labels": operator_record.labels,
                "connected_at": datetime.now(UTC).isoformat(),
            },
        )

        # Send registration confirmation
        await websocket.send_json({
            "type": "registered",
            "operator_id": operator_id,
            "cluster_name": cluster_name,
            "message": f"Successfully registered as '{cluster_name}'",
        })

        logger.info(f"Operator registered: cluster={cluster_name}, operator_id={operator_id}")

    except Exception as e:
        logger.error(f"Operator registration failed: {e}")
        try:
            await websocket.close(code=4000, reason=f"Registration failed: {e}")
        except Exception:
            pass
        db.close()
        return
    finally:
        db.close()

    # --- Step 3: Message loop ---
    try:
        while True:
            try:
                message = await asyncio.wait_for(websocket.receive_json(), timeout=90.0)
            except TimeoutError:
                # No message in 90s — send ping to check if alive
                try:
                    await websocket.send_json({"type": "ping"})
                    continue
                except Exception:
                    logger.warning(f"Operator {operator_id}: ping failed, disconnecting")
                    break

            msg_type = message.get("type")

            if msg_type == "heartbeat":
                # Lightweight heartbeat — update in-memory + periodic DB flush
                db = SessionLocal()
                try:
                    update_operator_heartbeat(
                        db=db,
                        operator_id=operator_id,
                        operator_version=message.get("operator_version"),
                        uptime_seconds=message.get("uptime_seconds"),
                        commands_executed=message.get("commands_executed"),
                    )
                finally:
                    db.close()

                # Acknowledge heartbeat
                await websocket.send_json({"type": "pong"})

            elif msg_type == "health":
                # Full health report — persist to DB
                db = SessionLocal()
                try:
                    update_operator_health(
                        db=db,
                        operator_id=operator_id,
                        health_report=message,
                    )
                finally:
                    db.close()

                logger.debug(f"Operator {operator_id}: health report received")

            elif msg_type == "llm_metrics":
                # In-cluster LLM backend metrics — store in Redis keyed by cluster
                analyzer = message.get("analyzer") or {}
                data = message.get("data") or {}
                ns = analyzer.get("namespace", "")
                an = analyzer.get("name", "")
                if ns and an and data:
                    from services.llm_metrics_service import store_llm_metrics

                    db = SessionLocal()
                    try:
                        store_llm_metrics(db, operator_id, ns, an, data)
                    finally:
                        db.close()
                else:
                    logger.debug(
                        "Operator %s: llm_metrics message missing analyzer/data fields",
                        operator_id,
                    )

            elif msg_type in ("output", "result", "error"):
                # Command-related messages — route to connection manager
                await operator_connections.handle_message(operator_id, message)

            elif msg_type == "pong":
                # Response to our ping — connection is alive
                pass

            else:
                logger.warning(f"Operator {operator_id}: unknown message type '{msg_type}'")

    except WebSocketDisconnect as e:
        logger.info(f"Operator {operator_id} WebSocket disconnected: code={e.code}")
    except Exception as e:
        logger.error(f"Operator {operator_id} WebSocket error: {e}")
    finally:
        # Clean up
        await operator_connections.unregister(operator_id, reason="websocket_closed")

        db = SessionLocal()
        try:
            mark_operator_disconnected(db, operator_id, reason="websocket_closed")
        except Exception as e:
            logger.error(f"Error marking operator disconnected: {e}")
        finally:
            db.close()

        logger.info(f"Operator {operator_id} session ended")
