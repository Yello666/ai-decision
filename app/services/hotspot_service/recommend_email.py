"""
推荐热点结果邮件发送。

将 /hotspot/recommend 的返回数据整理为邮件正文并发送给商户邮箱。
"""
from __future__ import annotations

import logging
from datetime import datetime

from app.core.email_sender import EmailSender
from app.schemas.hotspot import HotspotRecommendedItem

logger = logging.getLogger(__name__)


def _format_product_opportunities_text(item: HotspotRecommendedItem) -> str:
    opportunities = item.trend.product_opportunities or []
    if not opportunities:
        return "暂无"
    parts: list[str] = []
    for opportunity in opportunities:
        selling_points = "、".join(opportunity.selling_points) if opportunity.selling_points else "暂无"
        parts.append(
            f"{opportunity.product_name}（人群：{opportunity.target_audience}；"
            f"原因：{opportunity.reason}；制作难度：{opportunity.production_difficulty}；"
            f"卖点：{selling_points}）"
        )
    return "；".join(parts)


def _format_product_opportunities_html(item: HotspotRecommendedItem) -> str:
    opportunities = item.trend.product_opportunities or []
    if not opportunities:
        return "暂无"
    rows = []
    for opportunity in opportunities:
        selling_points = "、".join(opportunity.selling_points) if opportunity.selling_points else "暂无"
        rows.append(
            f"<strong>{opportunity.product_name}</strong><br>"
            f"人群：{opportunity.target_audience}<br>"
            f"原因：{opportunity.reason}<br>"
            f"制作难度：{opportunity.production_difficulty}<br>"
            f"卖点：{selling_points}"
        )
    return "<hr style='border:none;border-top:1px solid #eee;margin:6px 0;'>".join(rows)


def _format_execution_feasibility(item: HotspotRecommendedItem) -> str:
    feasibility = item.match.execution_feasibility
    return f"{feasibility.score} - {feasibility.reason}"


def _build_text_body(
    *,
    merchant_name: str,
    items: list[HotspotRecommendedItem],
    analyzed_count: int,
    min_compatibility_score: float,
) -> str:
    lines: list[str] = [
        f"{merchant_name}，您好：",
        "",
        "这是本次热点推荐分析结果：",
        "- 匹配范围: 全量缓存热点（与列表接口同源，最多 50 条）",
        f"- 最低筛选分数: {min_compatibility_score}",
        f"- 入选条数: {len(items)}",
        "",
    ]
    for idx, item in enumerate(items, start=1):
        lines.extend(
            [
                f"{idx}. {item.trend.title}",
                f"   匹配分: {item.match.compatibility_score}",
                f"   推荐等级: {item.match.recommendation.value}",
                f"   推荐原因: {item.match.reason}",
                f"   营销建议: {item.match.suggestion}",
                f"   商品机会: {_format_product_opportunities_text(item)}",
                f"   可执行性: {_format_execution_feasibility(item)}",
                "   匹配链接: 链接功能未完善，敬请期待",
                f"   跳转链接: {item.trend.jump_url}",
                "",
            ]
        )

    lines.extend(
        [
            "——",
            "此邮件由系统自动发送，请勿直接回复。",
        ]
    )
    return "\n".join(lines)


def _build_html_body(
    *,
    merchant_name: str,
    items: list[HotspotRecommendedItem],
    analyzed_count: int,
    min_compatibility_score: float,
) -> str:
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    rows = []
    for idx, item in enumerate(items, start=1):
        rows.append(
            f"""
            <tr>
              <td style="padding:8px;border:1px solid #ddd;">{idx}</td>
              <td style="padding:8px;border:1px solid #ddd;">{item.trend.title}</td>
              <td style="padding:8px;border:1px solid #ddd;">{item.match.compatibility_score}</td>
              <td style="padding:8px;border:1px solid #ddd;">{item.match.recommendation.value}</td>
              <td style="padding:8px;border:1px solid #ddd;">{item.match.reason}</td>
              <td style="padding:8px;border:1px solid #ddd;">{item.match.suggestion}</td>
              <td style="padding:8px;border:1px solid #ddd;">{_format_product_opportunities_html(item)}</td>
              <td style="padding:8px;border:1px solid #ddd;">{_format_execution_feasibility(item)}</td>
              <td style="padding:8px;border:1px solid #ddd;">链接功能未完善，敬请期待</td>
              <td style="padding:8px;border:1px solid #ddd;">
                <a href="{item.trend.jump_url}" target="_blank">查看热点</a>
              </td>
            </tr>
            """
        )

    table_rows = "\n".join(rows) if rows else (
        "<tr><td colspan='10' style='padding:8px;border:1px solid #ddd;'>"
        "无符合阈值的热点，请适当降低筛选分数后重试。"
        "</td></tr>"
    )

    return f"""
    <div style="font-family:Arial,Helvetica,sans-serif;color:#222;">
      <p>{merchant_name}，您好：</p>
      <p>这是本次热点推荐分析结果（{now_str}）：</p>
      <ul>
        <li>匹配范围：全量缓存热点（与列表接口同源，最多 50 条）</li>
        <li>最低筛选分数：{min_compatibility_score}</li>
        <li>入选条数：{len(items)}</li>
      </ul>
      <table style="border-collapse:collapse;width:100%;font-size:13px;">
        <thead>
          <tr style="background:#f5f5f5;">
            <th style="padding:8px;border:1px solid #ddd;">#</th>
            <th style="padding:8px;border:1px solid #ddd;">热点</th>
            <th style="padding:8px;border:1px solid #ddd;">匹配分</th>
            <th style="padding:8px;border:1px solid #ddd;">推荐等级</th>
            <th style="padding:8px;border:1px solid #ddd;">推荐原因</th>
            <th style="padding:8px;border:1px solid #ddd;">营销建议</th>
            <th style="padding:8px;border:1px solid #ddd;">商品机会</th>
            <th style="padding:8px;border:1px solid #ddd;">可执行性</th>
            <th style="padding:8px;border:1px solid #ddd;">匹配链接</th>
            <th style="padding:8px;border:1px solid #ddd;">链接</th>
          </tr>
        </thead>
        <tbody>
          {table_rows}
        </tbody>
      </table>
      <p style="margin-top:16px;color:#666;">此邮件由系统自动发送，请勿直接回复。</p>
    </div>
    """


def send_recommendation_email(
    *,
    merchant_email: str,
    merchant_name: str,
    items: list[HotspotRecommendedItem],
    analyzed_count: int,
    min_compatibility_score: float,
) -> bool:
    """发送推荐热点结果邮件。发送失败返回 False。"""
    subject = f"热点推荐结果 - {merchant_name}"
    sender = EmailSender()
    text_body = _build_text_body(
        merchant_name=merchant_name,
        items=items,
        analyzed_count=analyzed_count,
        min_compatibility_score=min_compatibility_score,
    )
    html_body = _build_html_body(
        merchant_name=merchant_name,
        items=items,
        analyzed_count=analyzed_count,
        min_compatibility_score=min_compatibility_score,
    )
    ok = sender.send(
        to_emails=[merchant_email],
        subject=subject,
        text_body=text_body,
        html_body=html_body,
    )
    if not ok:
        logger.warning("热点推荐邮件发送失败 merchant=%s email=%s", merchant_name, merchant_email)
    return ok
