from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.responses import success
from app.db.mysql import get_db
from app.schemas.product_select import (
    AggregateRunResponse,
    InstagramRunRequest,
    InstagramRunResponse,
    MonitorCreateRequest,
    MonitorListResponse,
    MonitorOut,
    MonitorUpdateRequest,
    ProductSelectMatchListResponse,
    ProductSelectObjectListResponse,
    ProductMatchRefreshRequest,
    ProductMatchResponse,
    ProductMatchTestRequest,
    ProductMatchTestResponse,
    SummaryResponse,
)
from app.services.productselect_service import api_service

router = APIRouter(prefix="/product-select", tags=["product-select"])


@router.get("/monitors", summary="查询监控账号/频道列表")
def get_monitors(
    platform: str | None = Query(default=None, description="instagram/youtube 等"),
    is_enabled: bool | None = Query(default=None, description="是否启用"),
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
):
    data = api_service.query_monitors(
        db,
        platform=platform,
        is_enabled=is_enabled,
        limit=limit,
    )
    return success(MonitorListResponse(**data))


@router.post("/monitors", summary="新增或更新监控账号/频道")
def create_monitor(
    payload: MonitorCreateRequest,
    db: Session = Depends(get_db),
):
    return success(MonitorOut(**api_service.create_monitor(payload, db)))


@router.patch("/monitors/{monitor_id}", summary="更新监控账号/频道")
def update_monitor(
    monitor_id: int,
    payload: MonitorUpdateRequest,
    db: Session = Depends(get_db),
):
    data = api_service.update_monitor_by_id(monitor_id, payload, db)
    if data is None:
        raise HTTPException(status_code=404, detail="monitor_not_found")
    return success(MonitorOut(**data))


@router.delete("/monitors/{monitor_id}", summary="停用监控账号/频道")
def disable_monitor(
    monitor_id: int,
    db: Session = Depends(get_db),
):
    data = api_service.disable_monitor_by_id(monitor_id, db)
    if data is None:
        raise HTTPException(status_code=404, detail="monitor_not_found")
    return success(MonitorOut(**data))


@router.post("/instagram/run", summary="运行 Instagram 名人监控")
def run_instagram_monitor(
    payload: InstagramRunRequest,
    db: Session = Depends(get_db),
):
    return success(InstagramRunResponse(**api_service.run_instagram_monitor(payload, db)))


@router.post("/aggregate/run", summary="聚合 recognition.json 为 summary.json/csv")
def run_aggregate():
    result = api_service.run_aggregate_to_files()
    data = AggregateRunResponse(
        summary_json=result["summary_json"],
        summary_csv=result["summary_csv"],
        stats=result["stats"],
    )
    message = "no productSelect data found" if result.get("empty") else "success"
    return success(data, message=message)


@router.get("/summary", summary="读取聚合后的选品总表")
def get_summary(
    platform: str | None = Query(default=None, description="按平台过滤，如 instagram/youtube"),
    account: str | None = Query(default=None, description="按账号/频道过滤"),
    potential: str | None = Query(default=None, description="按潜力过滤，如 high/medium/low"),
    limit: int = Query(default=200, ge=1, le=2000, description="最多返回条数"),
):
    try:
        data = api_service.read_summary(
            platform=platform,
            account=account,
            potential=potential,
            limit=limit,
        )
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="summary.json 不存在，请先调用 /product-select/aggregate/run")
    return success(SummaryResponse(**data))


@router.post("/matches/test", summary="商品匹配测试：bbox 裁剪 + OSS + Google Lens")
def run_match_test(payload: ProductMatchTestRequest):
    return success(ProductMatchTestResponse(**api_service.run_supply_test(payload)))


@router.get("/matches/test/result/{image_stem}", summary="读取某张图的商品匹配测试结果")
def get_match_test_result(image_stem: str):
    try:
        data = api_service.read_supply_result(image_stem)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="商品匹配测试结果不存在")
    return success(data)


@router.get("/objects", summary="从数据库查询识图商品机会")
def get_objects(
    potential: str | None = Query(default=None, description="high/medium/low"),
    related_ip: str | None = Query(default=None, description="关联名人/IP"),
    category: str | None = Query(default=None, description="品类"),
    include_test: bool = Query(default=False, description="是否包含商品匹配测试产生的测试对象"),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    data = api_service.query_objects(
        db,
        potential=potential,
        related_ip=related_ip,
        category=category,
        include_test=include_test,
        limit=limit,
        offset=offset,
    )
    return success(ProductSelectObjectListResponse(**data))


@router.get("/objects/{object_id}/matches", summary="查询某个商品机会的商品匹配")
def get_object_matches(
    object_id: int,
    source: str | None = Query(default=None, description="google_lens/amazon/taobao/1688 等"),
    limit: int = Query(default=3, ge=1, le=500),
    db: Session = Depends(get_db),
):
    data = api_service.query_matches(db, object_id=object_id, source=source, limit=limit)
    return success(ProductSelectMatchListResponse(**data))


@router.post("/objects/{object_id}/matches/refresh", summary="刷新某个商品机会的商品匹配")
def refresh_object_matches(
    object_id: int,
    payload: ProductMatchRefreshRequest,
    db: Session = Depends(get_db),
):
    try:
        data = api_service.refresh_object_matches(
            db,
            object_id=object_id,
            lens_type=payload.lens_type,
            limit=payload.limit,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if data is None:
        raise HTTPException(status_code=404, detail="object_not_found")
    return success(ProductMatchResponse(**data))
