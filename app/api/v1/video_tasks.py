from typing import Optional

from fastapi import APIRouter,  Query, WebSocket

from app.api.deps import extract_access_token_from_cookies
from app.services.generate_service import handle_generation_status_ws
router = APIRouter(prefix="/video-tasks", tags=["video-tasks"])
# ----------------------------------------------------------
# WebSocket — 实时推送视频生成状态变更到前端
# ws(s)://host/api/v1/video-tasks/stream
#
# 鉴权优先级（与 REST 保持一致）：
#   1. HttpOnly Cookie: ``access_token``（推荐，前端 new WebSocket(url) 即可，浏览器自动携带同源 Cookie）
#   2. Query ``?token=<JWT>``（过渡期兼容，计划废弃；不建议新客户端使用，token 会落进网关/代理 access log）
# 通道：每个商户订阅独立 Redis channel gen:status:{store_id}
#
# 心跳协议（仅接受 JSON 文本帧）：
#   服务端 → 客户端: {"event":"ping"}   连接建立后立即发一次，之后每 WS_HEARTBEAT_INTERVAL_SECONDS 秒
#   客户端 → 服务端: {"event":"pong"}   收到 ping 后尽快回复
#   客户端主动下线 : {"event":"close"}  收到后服务端清理并断开
#   任意带合法 event 的 JSON 对象（除 close 外）均刷新「客户端活跃时间」
# 若超过 WS_PONG_TIMEOUT_SECONDS 未刷新活跃时间，服务端判定离线并断开。
# ----------------------------------------------------------
@router.websocket("/stream")
async def ws_generation_status(
    websocket: WebSocket,
    token: Optional[str] = Query(default=None),
):
    access_token = extract_access_token_from_cookies(websocket.cookies, token)
    await handle_generation_status_ws(websocket, access_token)
